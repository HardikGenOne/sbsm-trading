from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
from typing import Literal
import uuid

import httpx

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import OperationalError

from .config import Settings, SYMBOLS
from .database import Database
from .engine import PaperEngine, RateLimited, SignalConflict, now_ms
from .market import MarketCalendar, feed_configured, quote_feed
from .models import Account, Audit, Conflict, Event, Fill, Ledger, Notification, Order, Position, WebhookReceipt
from .schemas import QuoteInput, Signal
from .readiness import report as readiness_report
from .sessions import BrowserSessions, COOKIE
from .notifications import enqueue
from .ingress import client_ip


def create_app(settings=None, database=None, calendar=None, clock=now_ms):
    settings = settings or Settings()
    db = database or Database(settings.database_url)
    engine = PaperEngine(db, settings, calendar or MarketCalendar(settings.closed_dates), clock)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize(settings.initial_capital_paise, clock())
        yield
        if database is None:
            db.engine.dispose()

    production = settings.environment == "production"
    app = FastAPI(title="SBSM Paper Trading Brain", version="0.1.0", lifespan=lifespan, docs_url=None if production else "/docs", redoc_url=None if production else "/redoc", openapi_url=None if production else "/openapi.json")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.state.engine = engine
    sessions = BrowserSessions(settings.admin_token.get_secret_value(), clock, settings.session_seconds)
    bearer = HTTPBearer(auto_error=False)

    def admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials:
            if not hmac.compare_digest(credentials.credentials, settings.admin_token.get_secret_value()):
                raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
            return
        session = sessions.verify(request.cookies.get(COOKIE))
        if not session:
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            expected_origins = {settings.public_base_url.rstrip("/")} if production else {str(request.base_url).rstrip("/")}
            if settings.dashboard_base_url:
                expected_origins.add(settings.dashboard_base_url.rstrip("/"))
            if request.headers.get("origin") not in expected_origins or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf_token"]):
                raise HTTPException(403, "Same-origin request and CSRF token required")

    @app.middleware("http")
    async def response_security(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api/", "/auth/", "/controls/")):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
        return response

    @app.post("/auth/session")
    def login(response: Response, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not hmac.compare_digest(credentials.credentials, settings.admin_token.get_secret_value()):
            raise HTTPException(401, "Invalid admin token")
        cookie, session = sessions.issue()
        response.set_cookie(COOKIE, cookie, max_age=settings.session_seconds, httponly=True, secure=settings.session_cookie_secure, samesite="strict", path="/")
        return session

    @app.get("/auth/session")
    def session_status(request: Request):
        return sessions.verify(request.cookies.get(COOKIE)) or {"authenticated": False}

    @app.delete("/auth/session", dependencies=[Depends(admin)])
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=settings.session_cookie_secure, samesite="strict")
        return {"authenticated": False}

    @app.exception_handler(OperationalError)
    async def busy(request, exc):
        # Never acknowledge an uncommitted signal; an eventual retry is idempotent.
        return JSONResponse(status_code=503, content={"detail": "Database unavailable or busy; request not acknowledged"}, headers={"Retry-After": "1"})

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": [{"loc": e["loc"], "message": e["msg"]} for e in exc.errors()]})

    @app.get("/health")
    def health():
        return {"service": "sbsm-paper", "mode": "paper"}

    @app.api_route("/health/ready", methods=["GET", "HEAD"])
    def operational_health(response: Response):
        with db.read() as s:
            account = s.get(Account, 1)
            recent = bool(account and 0 <= clock() - account.worker_heartbeat_ms < 30000)
        response.status_code = 200 if recent else 503
        return {"service": "sbsm-paper", "worker_recent": recent}

    async def tradingview_body(token: str, request: Request):
        if not hmac.compare_digest(token, settings.webhook_token.get_secret_value()):
            raise HTTPException(401, "Invalid webhook credential")
        if settings.webhook_allowed_ips and client_ip(request, settings.webhook_client_ip_source) not in settings.webhook_allowed_ips:
            raise HTTPException(403, "Webhook source not allowed")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise HTTPException(413, "Signal exceeds 16 KB")
        return body

    @app.post("/webhooks/tradingview/{token}", status_code=202)
    async def webhook(token: str, request: Request):
        body = await tradingview_body(token, request)
        try:
            signal = Signal.model_validate(json.loads(body))
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, "Invalid SBSM signal contract") from exc
        # The body is durable before returning 202. Disk I/O runs outside the event loop.
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(engine.ingest, signal)
        except SignalConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except RateLimited as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    class TradingViewTest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        test_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,48}$")

    def record_tradingview_test(test_id: str):
        key = "tv-test:" + test_id
        with db.write() as (s, _):
            existing = s.scalar(select(Notification).where(Notification.dedupe_key == key))
            if existing:
                return {"reference": key, "received_ms": existing.created_ms, "duplicate": True}
            now = clock()
            recent = s.scalar(select(func.count()).select_from(Notification).where(
                Notification.dedupe_key.startswith("tv-test:"), Notification.created_ms >= now - 60000,
            ))
            if recent >= 5:
                raise HTTPException(429, "At most five test alerts per minute", headers={"Retry-After": "60"})
            enqueue(s, settings, key, "TRADINGVIEW TEST", ["TradingView webhook received.", "No paper order was created."], now)
        return {"reference": key, "received_ms": now, "duplicate": False}

    @app.post("/webhooks/tradingview/{token}/test", status_code=202)
    async def tradingview_test(token: str, request: Request):
        body = await tradingview_body(token, request)
        try:
            test = TradingViewTest.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, "Use a unique test_id containing 8–48 letters, digits, underscores or hyphens") from exc
        if not settings.telegram_enabled:
            raise HTTPException(409, "Configure and enable Telegram first")
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(record_tradingview_test, test.test_id)

    @app.get("/api/portfolio", dependencies=[Depends(admin)])
    def portfolio():
        return engine.portfolio()

    @app.get("/api/ingress", dependencies=[Depends(admin)])
    def ingress_status(request: Request):
        address = client_ip(request, settings.webhook_client_ip_source)
        return {"source": settings.webhook_client_ip_source, "client_ip": address,
                "tradingview_source_allowed": address in settings.webhook_allowed_ips}

    @app.get("/api/status", dependencies=[Depends(admin)])
    def status():
        account = engine.portfolio()
        age = clock() - account["worker_heartbeat_ms"]
        with db.read() as s:
            latest = s.scalars(select(Audit).order_by(Audit.id.desc()).limit(10)).all()
            audits = [row_dict(row) for row in latest]
        return {"mode": "paper", "feed_mode": account["feed_mode"], "worker_recent": 0 <= age < max(30000, int(settings.poll_seconds * 10000)), "market_open": engine.calendar.is_open(clock()), "kite_credentials_configured": bool(settings.kite_api_key.get_secret_value() and settings.kite_access_token.get_secret_value()), "recent_audit": audits, "entries_paused": account["entries_paused"], "reconciliation_required": account["reconciliation_required"]}

    @app.get("/api/readiness", dependencies=[Depends(admin)])
    def readiness():
        return readiness_report(engine)

    class FeedSelection(BaseModel):
        provider: Literal["kite", "dhan"]

    @app.post("/controls/market-data", dependencies=[Depends(admin)])
    def select_market_data(selection: FeedSelection):
        if not feed_configured(settings, selection.provider):
            raise HTTPException(409, "Configure this provider’s credentials on the server first")
        engine.select_feed(selection.provider)
        return {"feed_mode": selection.provider}

    @app.get("/api/market-data/quote", dependencies=[Depends(admin)])
    def test_market_data(provider: Literal["kite", "dhan"], symbol: str = Query(max_length=36)):
        symbol = symbol.strip().upper().removeprefix("NSE:")
        if symbol == "BAJAJ-AUTO":
            symbol = "BAJAJ_AUTO"
        if symbol not in SYMBOLS:
            raise HTTPException(422, "Enter an NSE stock from the SBSM watchlist")
        if not feed_configured(settings, provider):
            raise HTTPException(409, "This provider’s credentials are missing")
        feed = quote_feed(settings, provider)
        try:
            quotes = feed.fetch([symbol])
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                raise HTTPException(502, "Broker rejected the credentials or market-data permissions") from None
            if code == 429:
                raise HTTPException(429, "Broker rate limit reached. Retry in a few seconds.") from None
            raise HTTPException(502, "Broker quote request failed") from None
        except (httpx.HTTPError, ArithmeticError, KeyError, TypeError, ValueError):
            raise HTTPException(502, "Broker did not return usable market data") from None
        finally:
            feed.close()
        if not quotes:
            return {"provider": provider, "symbol": symbol, "quote": None}
        quote = quotes[0]
        age_ms = clock() - quote.asof_ms
        return {"provider": provider, "symbol": symbol, "quote": quote.model_dump(mode="json"),
                "age_ms": age_ms, "fresh": 0 <= age_ms <= settings.quote_max_age_seconds * 1000}

    @app.get("/api/trades", dependencies=[Depends(admin)])
    def closed_trades(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), order: Literal["asc", "desc"] = "asc"):
        with db.read() as s:
            ordering = (Position.closed_ms, Position.cycle_id) if order == "asc" else (Position.closed_ms.desc(), Position.cycle_id.desc())
            values = s.scalars(select(Position).where(Position.closed_ms.is_not(None)).order_by(*ordering).offset(offset).limit(limit)).all()
            return {"items": [row_dict(row) for row in values], "next_offset": offset + len(values), "money_unit": "paise"}

    @app.get("/api/summary", dependencies=[Depends(admin)])
    def counts():
        with db.read() as s:
            return {"signals": s.scalar(select(func.count()).select_from(Event)), "skipped": s.scalar(select(func.count()).select_from(Event).where(Event.status == "SKIPPED")), "active_orders": s.scalar(select(func.count()).select_from(Order).where(Order.status.in_(("PENDING", "PARTIALLY_FILLED")))), "closed_trades": s.scalar(select(func.count()).select_from(Position).where(Position.closed_ms.is_not(None)))}

    @app.get("/api/signals/{sequence}/trace", dependencies=[Depends(admin)])
    def signal_trace(sequence: int):
        with db.read() as s:
            event = s.get(Event, sequence)
            if not event:
                raise HTTPException(404, "Signal not found")
            orders = s.scalars(select(Order).where(Order.cycle_id == event.cycle_id).order_by(Order.id)).all()
            ids = [order.id for order in orders]
            fills = s.scalars(select(Fill).where(Fill.order_id.in_(ids)).order_by(Fill.id)).all() if ids else []
            ledger = s.scalars(select(Ledger).where(Ledger.order_id.in_(ids)).order_by(Ledger.id)).all() if ids else []
            cycle_signals = s.scalars(select(Event).where(Event.cycle_id == event.cycle_id).order_by(Event.id)).all()
            position = s.get(Position, event.cycle_id)
            return {"signal": row_dict(event), "cycle_signals": [row_dict(row) for row in cycle_signals], "orders": [row_dict(row) for row in orders], "fills": [row_dict(row) for row in fills], "ledger": [row_dict(row) for row in ledger], "position": row_dict(position) if position else None, "money_unit": "paise"}

    @app.get("/api/notifications", dependencies=[Depends(admin)])
    def notification_status(limit: int = Query(30, ge=1, le=100)):
        with db.read() as s:
            counts = dict(s.execute(select(Notification.status, func.count()).group_by(Notification.status)).all())
            rows = s.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)).all()
            test_rows = s.scalars(select(Notification).where(Notification.dedupe_key.startswith("tv-test:"))
                                  .order_by(Notification.id.desc()).limit(10)).all()
            fields = ("id", "dedupe_key", "created_ms", "status", "attempts", "sent_ms", "error_code")
        return {
            "enabled": settings.telegram_enabled, "counts": counts,
            "items": [{key: getattr(row, key) for key in fields} for row in rows],
            "test_alerts": [{key: getattr(row, key) for key in fields} for row in test_rows],
        }

    @app.post("/controls/notifications/test", dependencies=[Depends(admin)], status_code=202)
    def notification_test():
        if not settings.telegram_enabled:
            raise HTTPException(409, "Configure and enable Telegram locally first")
        key = "test:" + str(uuid.uuid4())
        with db.write() as (s, _):
            enqueue(s, settings, key, "CONNECTION TEST", ["Notifications are connected to the configured chat.", "No signal or order was created."], clock())
            s.add(Audit(time_ms=clock(), action="TELEGRAM_TEST_QUEUED", detail=key))
        return {"status": "QUEUED", "reference": key}

    @app.post("/controls/notifications/{notification_id}/retry", dependencies=[Depends(admin)])
    def notification_retry(notification_id: int):
        if not settings.telegram_enabled:
            raise HTTPException(409, "Telegram is disabled")
        with db.write() as (s, _):
            row = s.get(Notification, notification_id)
            if not row:
                raise HTTPException(404, "Notification not found")
            if row.chat_id != settings.telegram_chat_id.get_secret_value() or row.bot_id != settings.telegram_bot_token.get_secret_value().split(":", 1)[0]:
                raise HTTPException(409, "Original destination differs; historical messages cannot be retargeted")
            if row.status not in ("FAILED", "BLOCKED"):
                raise HTTPException(409, "Only failed or blocked messages can be retried")
            row.status, row.attempts, row.due_ms, row.error_code = "PENDING", 0, clock(), None
            s.add(Audit(time_ms=clock(), action="TELEGRAM_RETRY", detail=f"Notification {row.id}"))
        return {"status": "QUEUED"}

    @app.get("/api/{collection}", dependencies=[Depends(admin)])
    def rows(collection: str, limit: int = Query(100, ge=1, le=500), after: int = Query(0, ge=0), status: str | None = None, order: Literal["asc", "desc"] = "asc"):
        models = {"signals": Event, "skipped": Event, "orders": Order, "fills": Fill, "ledger": Ledger, "audit": Audit, "conflicts": Conflict, "receipts": WebhookReceipt}
        model = models.get(collection)
        if model is None:
            raise HTTPException(404, "Unknown collection")
        statement = select(model).where(model.id > after).order_by(model.id if order == "asc" else model.id.desc()).limit(limit)
        if collection == "skipped":
            statement = statement.where(Event.status == "SKIPPED")
        if status and model in (Event, Order):
            statement = statement.where(model.status == status)
        with db.read() as s:
            values = [row_dict(row) for row in s.scalars(statement)]
        return {"items": values, "next_after": values[-1]["id"] if values else after, "money_unit": "paise"}

    class PauseInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        paused: bool

    class CloseInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        cycle_id: str = Field(min_length=1, max_length=1200)

    class AcknowledgeInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        note: str = Field(min_length=20, max_length=2000)

    @app.post("/controls/positions/close", dependencies=[Depends(admin)], status_code=202)
    def close_position(value: CloseInput):
        try:
            return engine.request_close(value.cycle_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/controls/reconciliation/acknowledge", dependencies=[Depends(admin)])
    def acknowledge(value: AcknowledgeInput):
        engine.acknowledge_reconciliation(value.note)
        return {"reconciliation_required": False}

    @app.post("/controls/entries", dependencies=[Depends(admin)])
    def pause(value: PauseInput):
        engine.pause(value.paused)
        return {"entries_paused": value.paused, "existing_orders_unchanged": True}

    @app.post("/controls/orders/{order_id}/cancel", dependencies=[Depends(admin)])
    def cancel(order_id: int):
        try:
            engine.cancel_entry(order_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"status": "cancelled_or_already_terminal"}

    @app.post("/paper/quotes", dependencies=[Depends(admin)])
    def quote(value: QuoteInput):
        try:
            quote_id = engine.quote(value, "manual", selection=("manual", 0))
            if quote_id is None:
                raise HTTPException(403, "Manual quote injection requires the manual feed")
            return {"quote_id": quote_id}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    static = Path(__file__).resolve().parent / "static"
    app.mount("/assets", StaticFiles(directory=static), name="dashboard-assets")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-store"})

    return app


def row_dict(row):
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}
