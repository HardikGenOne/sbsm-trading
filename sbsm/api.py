from contextlib import asynccontextmanager
import hmac
import json

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import OperationalError

from .config import Settings
from .database import Database
from .engine import PaperEngine, RateLimited, SignalConflict, now_ms
from .market import MarketCalendar
from .models import Audit, Conflict, Event, Fill, Ledger, Order, Position
from .schemas import QuoteInput, Signal
from .readiness import report as readiness_report


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
    bearer = HTTPBearer(auto_error=False)

    def admin(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not hmac.compare_digest(credentials.credentials, settings.admin_token.get_secret_value()):
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})

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
    def closed_trades(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        with db.read() as s:
            values = s.scalars(select(Position).where(Position.closed_ms.is_not(None)).order_by(Position.closed_ms, Position.cycle_id).offset(offset).limit(limit)).all()
            return {"items": [row_dict(row) for row in values], "next_offset": offset + len(values), "money_unit": "paise"}

    @app.get("/api/{collection}", dependencies=[Depends(admin)])
    def rows(collection: str, limit: int = Query(100, ge=1, le=500), after: int = Query(0, ge=0), status: str | None = None):
        models = {"signals": Event, "skipped": Event, "orders": Order, "fills": Fill, "ledger": Ledger, "audit": Audit, "conflicts": Conflict}
        model = models.get(collection)
        if model is None:
            raise HTTPException(404, "Unknown collection")
        statement = select(model).where(model.id > after).order_by(model.id).limit(limit)
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

    return app


def row_dict(row):
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}
