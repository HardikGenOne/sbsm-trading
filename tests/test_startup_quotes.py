import json
import httpx
from pydantic import SecretStr
from sqlalchemy import select, func

from sbsm.config import SYMBOLS
from sbsm.models import NotificationState, Notification, Event, Order, Fill, Ledger
from sbsm.readiness import report
from sbsm.schemas import QuoteInput
from sbsm.startup_quotes import warm_dhan, recover_startup


def prepare(engine, clock):
    engine.settings.feed_mode='dhan'
    engine.settings.dhan_client_id=SecretStr('client')
    engine.settings.dhan_access_token=SecretStr('private-token')
    engine.settings.telegram_enabled=True
    engine.settings.telegram_chat_id=SecretStr('12345')
    return QuoteInput(symbol='ICICIBANK',asof_ms=clock(),bid=999,ask=1000,
                      bid_quantity=100,ask_quantity=100,last_price=1000)


def assert_no_trading(engine):
    with engine.db.read() as session:
        for model in (Event,Order,Fill):
            assert session.scalar(select(func.count()).select_from(model))==0
        assert session.scalar(select(func.count()).select_from(Ledger))==1
    assert engine.portfolio()['cash_paise']==50_000_000
    assert engine.portfolio()['reserved_paise']==0


def test_startup_warms_all_symbols_before_processing_and_queues_status(system):
    engine,clock,_=system
    quote=prepare(engine,clock)
    class Feed:
        observed_ms=clock()
        def fetch(self,symbols):
            assert symbols==list(SYMBOLS)
            # The status transaction is complete before network work starts.
            with engine.db.write() as (session,_):
                assert session.get(NotificationState,'dhan_startup').value['status']=='WARMING'
            assert_no_trading(engine)
            return [quote]
    assert warm_dhan(engine,Feed(),('dhan',0),clock)
    with engine.db.read() as session:
        saved=session.get(NotificationState,'dhan_startup').value
        assert saved['status']=='READY'
        assert saved['fresh_count']==1
        assert saved['sample']['symbol']=='ICICIBANK'
        assert session.scalar(select(func.count()).select_from(Notification))==1
    assert next(c for c in report(engine)['checks'] if c['name']=='startup')['status']=='PASS'
    assert_no_trading(engine)


def test_failed_token_is_actionable_and_recovery_notifies_once(system):
    engine,clock,_=system
    quote=prepare(engine,clock)
    class Feed:
        def fetch(self,symbols):
            response=httpx.Response(401,request=httpx.Request('POST','https://api.dhan.co/v2/marketfeed/quote'))
            raise httpx.HTTPStatusError('private-token must never be exposed',request=response.request,response=response)
    assert not warm_dhan(engine,Feed(),('dhan',0),clock)
    with engine.db.read() as session:
        result=session.get(NotificationState,'dhan_startup').value
        assert result['status']=='FAILED' and result['error_code']==401
        assert 'Update the token' in result['message']
        assert 'private-token' not in json.dumps(result)
    assert next(c for c in report(engine)['checks'] if c['name']=='startup')['status']=='BLOCKED'
    assert recover_startup(engine,[quote],clock)
    assert recover_startup(engine,[quote],clock)
    with engine.db.read() as session:
        assert session.get(NotificationState,'dhan_startup').value['status']=='READY'
        assert session.scalar(select(func.count()).select_from(Notification))==2
    assert_no_trading(engine)


def test_closed_market_startup_is_waiting_until_session_has_fresh_quotes(system):
    engine,clock,calendar=system
    quote=prepare(engine,clock)
    calendar.opened=False
    class Feed:
        observed_ms=clock()
        def fetch(self,symbols):return [quote]
    assert not warm_dhan(engine,Feed(),('dhan',0),clock)
    with engine.db.read() as session:
        assert session.get(NotificationState,'dhan_startup').value['status']=='WAITING'
    assert not recover_startup(engine,[quote],clock)
    calendar.opened=True
    clock.advance(11000)
    assert not recover_startup(engine,[quote],clock)  # Never mark stale quotes ready.
    assert recover_startup(engine,[quote.model_copy(update={'asof_ms':clock()})],clock)
    assert_no_trading(engine)


def test_empty_startup_response_never_claims_ready(system):
    engine,clock,_=system
    prepare(engine,clock)
    class Feed:
        def fetch(self,symbols):return []
    assert not warm_dhan(engine,Feed(),('dhan',0),clock)
    with engine.db.read() as session:
        assert session.get(NotificationState,'dhan_startup').value['fresh_count']==0
        assert session.get(NotificationState,'dhan_startup').value['status']=='WAITING'
    assert_no_trading(engine)
