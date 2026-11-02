from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import OperationalError

from .config import Settings
from .database import Database
from .engine import PaperEngine, RateLimited, SignalConflict, now_ms
from .market import MarketCalendar
from .models import Audit, Conflict, Event, Fill, Ledger, Order, Position
from .schemas import QuoteInput, Signal
from .readiness import report as readiness_report
from .sessions import BrowserSessions, COOKIE


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

    app = FastAPI(title="SBSM Paper Trading Brain", version="0.1.0", lifespan=lifespan)
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
            expected_origin = str(request.base_url).rstrip("/")
            if request.headers.get("origin") != expected_origin or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf_token"]):
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

    @app.post("/webhooks/tradingview/{token}", status_code=202)
    async def webhook(token: str, request: Request):
        if not hmac.compare_digest(token, settings.webhook_token.get_secret_value()):
            raise HTTPException(401, "Invalid webhook credential")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise HTTPException(413, "Signal exceeds 16 KB")
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

    @app.get("/api/portfolio", dependencies=[Depends(admin)])
    def portfolio():
        return engine.portfolio()

    @app.get("/api/status", dependencies=[Depends(admin)])
    def status():
        account = engine.portfolio()
        age = clock() - account["worker_heartbeat_ms"]
        with db.read() as s:
            latest = s.scalars(select(Audit).order_by(Audit.id.desc()).limit(10)).all()
            audits = [row_dict(row) for row in latest]
        return {"mode": "paper", "feed_mode": settings.feed_mode, "worker_recent": 0 <= age < max(30000, int(settings.poll_seconds * 10000)), "market_open": engine.calendar.is_open(clock()), "kite_credentials_configured": bool(settings.kite_api_key.get_secret_value() and settings.kite_access_token.get_secret_value()), "recent_audit": audits, "entries_paused": account["entries_paused"], "reconciliation_required": account["reconciliation_required"]}

    @app.get("/api/readiness", dependencies=[Depends(admin)])
    def readiness():
        return readiness_report(engine)

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

    @app.get("/api/{collection}", dependencies=[Depends(admin)])
    def rows(collection: str, limit: int = Query(100, ge=1, le=500), after: int = Query(0, ge=0), status: str | None = None, order: Literal["asc", "desc"] = "asc"):
        models = {"signals": Event, "skipped": Event, "orders": Order, "fills": Fill, "ledger": Ledger, "audit": Audit, "conflicts": Conflict}
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
        if settings.feed_mode != "manual":
            raise HTTPException(403, "Manual quote injection is disabled in Kite feed mode")
        try:
            return {"quote_id": engine.quote(value, "manual")}
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
