from fastapi.testclient import TestClient
import pytest

from sbsm.api import create_app
from sbsm.schemas import Signal
from conftest import make_signal, tick


def test_auth_contract_and_acknowledgement(system):
    engine, clock, calendar = system
    app = create_app(engine.settings, engine.db, calendar, clock)
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "paper"
        assert client.get("/api/portfolio").status_code == 401
        auth = {"Authorization": "Bearer " + "a" * 40}
        assert client.get("/api/portfolio", headers=auth).json()["pool_paise"] == 50_000_000
        signal = make_signal(engine).model_dump(mode="json")
        assert client.post("/webhooks/tradingview/wrong", json=signal).status_code == 401
        url = "/webhooks/tradingview/" + "b" * 40
        response = client.post(url, json=signal)
        assert response.status_code == 202
        assert response.json()["status"] == "QUEUED"
        assert client.post(url, json=signal).json()["duplicate"]
        assert client.post(url, json=signal | {"quantity": 10}).status_code == 422
        assert client.post(url, content="x" * 17000).status_code == 413
        assert client.post(url, json=signal | {"reference_price": 1001}).status_code == 409
        assert len(client.get("/api/conflicts", headers=auth).json()["items"]) == 1
        engine.step()
        clock.advance()
        tick(engine)
        engine.step()
        assert len(client.get("/api/portfolio", headers=auth).json()["positions"]) == 1
        assert client.get("/api/signals?limit=1", headers=auth).json()["next_after"] == 1
        assert client.post("/controls/entries", json={"paused": True}, headers=auth).status_code == 200


@pytest.mark.parametrize("patch", [
    {"symbol": "INFY"}, {"ticker_id": "NSE:SBIN"}, {"action": "SELL"},
    {"signal_bar_close_ms": "1793598300000"}, {"signal_bar_close_ms": True},
    {"reference_price": "NaN"}, {"reason": "SBSM_SELL"}, {"trade_cycle_id": "unknown"},
])
def test_malformed_signals_rejected(system, patch):
    engine, _, _ = system
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Signal.model_validate(make_signal(engine).model_dump() | patch)


def test_kite_mode_blocks_manual_quotes(system):
    engine, clock, calendar = system
    engine.settings.feed_mode = "kite"
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as client:
        response = client.post("/paper/quotes", headers={"Authorization": "Bearer " + "a" * 40}, json={"symbol": "SBIN", "asof_ms": clock(), "bid": 100, "ask": 101, "bid_quantity": 100, "ask_quantity": 100, "last_price": 100})
        assert response.status_code == 403
