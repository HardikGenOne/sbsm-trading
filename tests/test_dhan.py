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
        assert json.loads(request.content) == {"NSE_EQ": [4963, 16669]}
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


def test_dashboard_switch_requires_credentials_and_quote_test_does_not_store(system, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from sbsm.api import create_app
    from sbsm.models import Quote
    from sbsm.schemas import QuoteInput

    engine, clock, calendar = system
    auth = {"Authorization": "Bearer " + "a" * 40}
    quote = QuoteInput(symbol="ICICIBANK", asof_ms=clock(), bid=1000, ask=1001,
                       bid_quantity=2, ask_quantity=3, last_price=1000)

    class DiagnosticFeed:
        def fetch(self, symbols):
            assert symbols == ["ICICIBANK"]
            return [quote]

        def close(self):
            pass

    monkeypatch.setattr("sbsm.api.quote_feed", lambda settings, provider: DiagnosticFeed())
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as client:
        selection = {"provider": "dhan"}
        assert client.post("/controls/market-data", json=selection).status_code == 401
        assert client.post("/controls/market-data", json=selection, headers=auth).status_code == 409
        engine.settings.dhan_client_id = SecretStr("test-client")
        engine.settings.dhan_access_token = SecretStr("test-token")
        result = client.get("/api/market-data/quote?provider=dhan&symbol=NSE:ICICIBANK", headers=auth)
        assert result.status_code == 200 and result.json()["quote"]["last_price"] == "1000"
        assert report(engine)["feed_mode"] == "manual"
        with engine.db.read() as session:
            assert session.scalar(select(func.count()).select_from(Quote)) == 0
        engine.quote(quote, "dhan")
        clock.advance()
        assert client.post("/controls/market-data", json=selection, headers=auth).status_code == 200
        assert report(engine)["feed_mode"] == "dhan"
        assert report(engine)["latest_quote_ms"] is None
        assert engine.quote(quote, "manual", selection=("manual", 0)) is None
        clock.advance()
        engine.quote(quote.model_copy(update={"asof_ms": clock()}), "dhan")
        assert report(engine)["latest_quote_ms"] == clock()


def cached_quote_response():
    stamp = datetime.now(IST).replace(microsecond=0)
    return {"status": "success", "data": {"NSE_EQ": {"4963": {
        "depth": {"buy": [{"price": 999, "quantity": 100}],
                  "sell": [{"price": 1000, "quantity": 100}]},
        "last_price": 1000,
        "last_trade_time": stamp.strftime("%d/%m/%Y %H:%M:%S"),
    }}}}


def instrument_response():
    return httpx.Response(200, text=(
        "EXCH_ID,SEGMENT,INSTRUMENT,SERIES,UNDERLYING_SYMBOL,SECURITY_ID,TICK_SIZE\n"
        "NSE,E,EQUITY,EQ,ICICIBANK,4963,10.0000\n"))


def configure_dhan(engine):
    engine.settings.dhan_client_id = SecretStr('test-client')
    engine.settings.dhan_access_token = SecretStr('test-token')


def test_separate_api_clients_reuse_instruments_and_original_market_time(system):
    engine, _, _ = system
    configure_dhan(engine)
    calls=[]
    def respond(request):
        calls.append(request.method)
        return instrument_response() if request.method=='GET' else httpx.Response(200,json=cached_quote_response())
    with httpx.Client(base_url='https://api.dhan.co',transport=httpx.MockTransport(respond)) as client:
        first=DhanQuotes(engine.settings,client).fetch(['ICICIBANK'])[0]
        second=DhanQuotes(engine.settings,client).fetch(['ICICIBANK'])[0]
    assert calls==['GET','POST']
    assert second==first  # Cache never replaces broker timestamps with fetch time.
    saved=next(engine.settings.market_cache_dir.glob('*.json')).read_text()
    assert 'test-token' not in saved and 'test-client' not in saved


def test_rate_limit_cooldown_shared_by_worker_and_diagnostic(system,monkeypatch):
    import sbsm.dhan as module
    engine, _, _=system
    configure_dhan(engine)
    current=[1000.0]
    monkeypatch.setattr(module.time,'time',lambda:current[0])
    calls=[]
    def respond(request):
        if request.method=='GET':return instrument_response()
        calls.append(request.method)
        if len(calls)==1:return httpx.Response(429,headers={'Retry-After':'3'})
        return httpx.Response(200,json=cached_quote_response())
    import pytest
    with httpx.Client(base_url='https://api.dhan.co',transport=httpx.MockTransport(respond)) as client:
        worker=DhanQuotes(engine.settings,client)
        with pytest.raises(httpx.HTTPStatusError):worker.fetch(['ICICIBANK'])
        diagnostic=DhanQuotes(engine.settings,client)
        with pytest.raises(httpx.HTTPStatusError) as blocked:diagnostic.fetch(['ICICIBANK'])
        assert blocked.value.response.status_code==429
        assert len(calls)==1
        current[0]+=4
        assert diagnostic.fetch(['ICICIBANK'])
        assert len(calls)==2


def test_token_rotation_cannot_reuse_previous_tokens_cache(system):
    import pytest
    engine, _, _=system
    configure_dhan(engine)
    def respond(request):
        if request.method=='GET':return instrument_response()
        if request.headers['access-token']=='test-token':return httpx.Response(200,json=cached_quote_response())
        return httpx.Response(401)
    with httpx.Client(base_url='https://api.dhan.co',transport=httpx.MockTransport(respond)) as client:
        assert DhanQuotes(engine.settings,client).fetch(['ICICIBANK'])
        engine.settings.dhan_access_token=SecretStr('new-invalid-token')
        with pytest.raises(httpx.HTTPStatusError) as rejected:DhanQuotes(engine.settings,client).fetch(['ICICIBANK'])
        assert rejected.value.response.status_code==401


def quote_process(settings, calls, output):
    def respond(request):
        with calls.get_lock():calls.value+=1
        if request.method=='GET':return instrument_response()
        return httpx.Response(200,json=cached_quote_response())
    try:
        with httpx.Client(base_url='https://api.dhan.co',transport=httpx.MockTransport(respond)) as client:
            output.put(len(DhanQuotes(settings,client).fetch(['ICICIBANK'])))
    except Exception as exc:output.put(type(exc).__name__)


def test_worker_and_api_processes_share_single_broker_request(system):
    import multiprocessing
    engine, _, _=system
    configure_dhan(engine)
    context=multiprocessing.get_context('fork')
    calls=context.Value('i',0)
    output=context.Queue()
    processes=[context.Process(target=quote_process,args=(engine.settings,calls,output)) for _ in range(2)]
    for process in processes:process.start()
    for process in processes:
        process.join(10)
        if process.is_alive():process.terminate();process.join();raise AssertionError('Quote coordination stalled')
        assert process.exitcode==0
    assert [output.get(timeout=2),output.get(timeout=2)]==[1,1]
    assert calls.value==2  # One instrument GET and one batched quote POST.


def test_diagnostic_distinguishes_credentials_rate_limit_and_temporary_failure(system,monkeypatch):
    from fastapi.testclient import TestClient
    from sbsm.api import create_app
    engine,clock,calendar=system
    configure_dhan(engine)
    status=[429]
    class FailingFeed:
        def fetch(self,symbols):
            response=httpx.Response(status[0],headers={'Retry-After':'4'},request=httpx.Request('POST','https://api.dhan.co/v2/marketfeed/quote'))
            response.raise_for_status()
        def close(self):pass
    monkeypatch.setattr('sbsm.api.quote_feed',lambda settings,provider:FailingFeed())
    with TestClient(create_app(engine.settings,engine.db,calendar,clock)) as client:
        for code,expected,word in [(429,429,'rate limit'),(503,503,'temporarily'),(401,502,'access token'),(403,502,'market-data access')]:
            status[0]=code
            response=client.get('/api/market-data/quote?provider=dhan&symbol=ICICIBANK',headers={'Authorization':'Bearer '+'a'*40})
            assert response.status_code==expected
            assert word in response.json()['detail']
            if code in (429,503):assert response.headers['Retry-After']=='4'
