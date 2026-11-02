import importlib
import json

from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
from sqlalchemy import func, select

from sbsm.api import create_app
from sbsm.models import Ledger
from sbsm.readiness import report
from sbsm.schemas import QuoteInput


def configure_kite(engine):
    engine.settings.feed_mode = "kite"
    engine.settings.kite_api_key = SecretStr("test-key")
    engine.settings.kite_access_token = SecretStr("test-session")


def live_tick(engine):
    return engine.quote(QuoteInput(symbol="ICICIBANK", asof_ms=engine.clock(), bid=100, ask=101, bid_quantity=100, ask_quantity=100, last_price=100), "kite")


def test_missing_credentials_and_stopped_worker_are_explicit(system):
    engine, _, _ = system
    engine.settings.feed_mode = "kite"
    before = engine.portfolio()
    result = report(engine)
    checks = {x["name"]: x for x in result["checks"]}
    assert not result["ready_for_new_entries"]
    assert checks["worker"]["status"] == "BLOCKED"
    assert checks["feed"]["status"] == "BLOCKED"
    assert engine.portfolio() == before
    with engine.db.read() as session:
        assert session.scalar(select(func.count()).select_from(Ledger)) == 1


def test_fresh_quote_running_worker_and_config_are_required(system):
    engine, clock, _ = system
    configure_kite(engine)
    engine.step()
    assert not report(engine)["ready_for_new_entries"]
    live_tick(engine)
    assert report(engine)["ready_for_new_entries"]
    clock.advance(11000)
    assert not report(engine)["ready_for_new_entries"]
    assert next(c for c in report(engine)["checks"] if c["name"] == "quotes")["status"] == "BLOCKED"


def test_closed_market_is_waiting_not_fake_ready(system):
    engine, _, calendar = system
    configure_kite(engine)
    calendar.opened = False
    engine.step()
    result = report(engine)
    assert not result["ready_for_new_entries"]
    assert next(c for c in result["checks"] if c["name"] == "market_session")["status"] == "WAITING"


def test_calendar_failure_is_reported_without_crashing(system):
    engine, _, calendar = system
    def unsupported(_):
        raise RuntimeError("Unsupported year")
    calendar.is_open = unsupported
    result = report(engine)
    assert not result["calendar_valid"]
    assert not result["ready_for_new_entries"]


def test_manual_quotes_never_claim_live_data_readiness(system):
    engine, _, _ = system
    engine.step()
    result = report(engine)
    assert not result["ready_for_new_entries"]
    assert next(c for c in result["checks"] if c["name"] == "feed")["status"] == "BLOCKED"


def test_readiness_route_authenticated_and_has_no_secret_fields(system):
    engine, clock, calendar = system
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as client:
        assert client.get("/api/readiness").status_code == 401
        response = client.get("/api/readiness", headers={"Authorization": "Bearer " + "a" * 40})
        assert response.status_code == 200
        assert response.json()["purpose"] == "live-data paper trading"
        assert "a" * 40 not in response.text
        assert "b" * 40 not in response.text


def test_read_only_probe_classifies_errors_and_redacts_broker_content(system):
    engine, _, _ = system
    module = importlib.import_module("scripts.check_readiness")
    class FakeFeed:
        configured = True
        closed = False
        def fetch(self, symbols):
            request = httpx.Request("GET", "https://api.kite.trade/quote")
            response = httpx.Response(403, request=request, text="must-not-leak")
            raise httpx.HTTPStatusError("must-not-leak", request=request, response=response)
        def close(self):
            self.closed = True
    feed = FakeFeed()
    result = module.probe_market_data(engine.settings, engine.clock, lambda _: feed)
    assert result["reason"] == "AUTHENTICATION_OR_PERMISSION_FAILURE"
    assert "must-not-leak" not in json.dumps(result)
    assert feed.closed


def test_read_only_probe_missing_credentials_does_not_fetch(system):
    engine, _, _ = system
    module = importlib.import_module("scripts.check_readiness")
    class FakeFeed:
        configured = False
        def fetch(self, symbols):
            raise AssertionError("No network call should be made")
        def close(self):
            pass
    result = module.probe_market_data(engine.settings, engine.clock, lambda _: FakeFeed())
    assert result["reason"] == "MISSING_CREDENTIALS"
