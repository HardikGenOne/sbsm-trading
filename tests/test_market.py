from datetime import datetime
from decimal import Decimal

import httpx
import pytest

from sbsm.market import KiteQuotes, MarketCalendar


def test_nse_session_weekend_and_override():
    calendar = MarketCalendar()
    during = int(datetime.fromisoformat("2026-11-02T11:15:00+05:30").timestamp() * 1000)
    after = int(datetime.fromisoformat("2026-11-02T15:30:00+05:30").timestamp() * 1000)
    saturday = int(datetime.fromisoformat("2026-11-07T11:15:00+05:30").timestamp() * 1000)
    assert calendar.is_open(during)
    assert not calendar.is_open(after)
    assert not calendar.is_open(saturday)
    assert not MarketCalendar(["2026-11-02"]).is_open(during)
    assert calendar.next_session_open(during) > after
    with pytest.raises(RuntimeError):
        calendar.session("2027-01-04")


def test_kite_adapter_uses_read_only_quotes_and_instrument_ticks(system):
    engine, _, _ = system
    # Construct a new validated settings instance to retain SecretStr types.
    settings = type(engine.settings)(**(engine.settings.model_dump() | {"kite_api_key": "key", "kite_access_token": "token"}), _env_file=None)
    feed = KiteQuotes(settings)
    calls = []

    def transport(request):
        calls.append(request.url.path)
        assert request.method == "GET"
        if request.url.path == "/instruments/NSE":
            return httpx.Response(200, text="tradingsymbol,segment,instrument_type,tick_size\nICICIBANK,NSE,EQ,0.01\n")
        assert request.url.path == "/quote"
        return httpx.Response(200, json={"data": {"NSE:ICICIBANK": {"timestamp": "2026-11-02 11:15:02", "last_price": 1000, "depth": {"buy": [{"price": 999, "quantity": 50}, {"price": 998, "quantity": 100}], "sell": [{"price": 1002, "quantity": 80}, {"price": 1001, "quantity": 60}]}}}})

    feed.client.close()
    feed.client = httpx.Client(base_url="https://api.kite.trade", transport=httpx.MockTransport(transport))
    quotes = feed.fetch(["ICICIBANK", "SBIN"])
    assert len(quotes) == 1
    assert quotes[0].tick_size == Decimal("0.01")
    assert quotes[0].bid == 999 and quotes[0].ask == 1001
    assert quotes[0].ask_quantity == 60
    feed.fetch(["ICICIBANK"])
    assert calls.count("/instruments/NSE") == 1
    feed.close()
