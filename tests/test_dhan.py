from datetime import datetime
from decimal import Decimal
import json

import httpx
from pydantic import SecretStr

from conftest import make_signal, tick
from sbsm.dhan import DhanQuotes, IST
from sbsm.readiness import report


def test_dhan_depth_reaches_engine_and_other_feeds_do_not_fill(system):
    engine, clock, _ = system
    engine.settings.feed_mode = "dhan"
    engine.settings.dhan_client_id = SecretStr("test-client")
    engine.settings.dhan_access_token = SecretStr("test-token")
    stamp = datetime.now(IST).replace(microsecond=0)
    clock.value = int(stamp.timestamp() * 1000)
    engine.ingest(make_signal(engine))
    engine.step()
    tick(engine)
    engine.step()
    assert engine.portfolio()["positions"] == []
    assert not report(engine)["ready_for_new_entries"]

    def respond(request):
        if request.method == "GET":
            assert request.url.path == "/v2/instrument/NSE_EQ"
            assert "access-token" not in request.headers
            return httpx.Response(200, text=(
                "EXCH_ID,SEGMENT,INSTRUMENT,SERIES,UNDERLYING_SYMBOL,SECURITY_ID,TICK_SIZE\n"
                "NSE,E,EQUITY,EQ,ICICIBANK,4963,10.0000\n"
                "NSE,E,EQUITY,EQ,BAJAJ-AUTO,16669,50.0000\n"
            ))
        assert request.url.path == "/v2/marketfeed/quote"
        assert request.headers["access-token"] == "test-token"
        assert json.loads(request.content) == {"NSE_EQ": [4963]}
        return httpx.Response(200, json={"status": "success", "data": {"NSE_EQ": {"4963": {
            "depth": {"buy": [{"price": 999.9, "quantity": 1000}],
                      "sell": [{"price": 1001, "quantity": 1000}, {"price": 1000, "quantity": 1000}]},
            "last_price": 1000,
            "last_trade_time": stamp.strftime("%d/%m/%Y %H:%M:%S"),
        }}}})

    with httpx.Client(base_url="https://api.dhan.co", transport=httpx.MockTransport(respond)) as client:
        feed = DhanQuotes(engine.settings, client)
        quote, = feed.fetch(["ICICIBANK"])
        assert feed.instruments["BAJAJ_AUTO"] == (16669, Decimal("0.50"))
    assert quote.tick_size == Decimal("0.10")
    assert quote.ask == Decimal("1000")
    clock.advance(11000)
    engine.quote(quote, "dhan")
    engine.step()
    assert engine.portfolio()["positions"] == []
    clock.advance()
    engine.quote(quote.model_copy(update={"asof_ms": clock()}), "dhan")
    engine.step()
    assert len(engine.portfolio()["positions"]) == 1
    assert report(engine)["ready_for_new_entries"]
