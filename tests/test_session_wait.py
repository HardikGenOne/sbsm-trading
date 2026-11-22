"""Session-boundary regressions: no auction fills, no overnight entry expiry."""
from datetime import datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from conftest import make_signal, tick
from sbsm.config import SYMBOLS
from sbsm.engine import PaperEngine
from sbsm.market import MarketCalendar, CAS_SYMBOLS
from sbsm.models import Event, Order, Fill, Notification
from sbsm.readiness import report


def ms(value):
    return int(datetime.fromisoformat(value + '+05:30').timestamp() * 1000)


def at(clock, value):
    clock.value = ms(value)


def rows(engine, model):
    with engine.db.read() as s:
        return s.scalars(select(model).order_by(model.id)).all()


def real_calendar(engine):
    engine.calendar = MarketCalendar()
    return engine.calendar


@pytest.mark.parametrize('symbol', SYMBOLS)
def test_current_stock_cutoffs_and_december_rule_change(symbol):
    calendar = MarketCalendar()
    cutoff = '15:15:00' if symbol in CAS_SYMBOLS else '15:30:00'
    assert calendar.continuous_session(symbol, '2026-11-23')[1] == ms('2026-11-23T' + cutoff)
    assert not calendar.is_continuous_open(symbol, ms('2026-11-23T' + cutoff))
    assert calendar.is_continuous_open(symbol, ms('2026-12-07T15:15:01'))
    assert not calendar.is_continuous_open(symbol, ms('2026-12-07T15:30:00'))
    assert calendar.continuous_session(symbol, '2026-07-31')[1] == ms('2026-07-31T15:30:00')


def test_weekends_holidays_and_special_session_do_not_use_regular_cutoff(monkeypatch):
    calendar = MarketCalendar(['2026-11-24'])
    assert calendar.next_continuous_open('EICHERMOT', ms('2026-11-20T15:15:01')) == ms('2026-11-23T09:15:00')
    assert calendar.next_continuous_open('EICHERMOT', ms('2026-11-23T15:15:01')) == ms('2026-11-25T09:15:00')
    assert calendar.next_continuous_open('EICHERMOT', ms('2026-11-23T08:00:00')) == ms('2026-11-23T09:15:00')
    monkeypatch.setattr(calendar, 'session', lambda day: (ms(day+'T18:00:00'), ms(day+'T19:00:00')))
    assert calendar.is_continuous_open('EICHERMOT', ms('2026-11-08T18:15:00'))
    assert not calendar.is_continuous_open('EICHERMOT', ms('2026-11-08T19:00:00'))


def test_closing_entry_reserves_waits_survives_restart_and_requires_open_quote(system):
    engine, clock, _ = system
    calendar = real_calendar(engine)
    at(clock, '2026-11-17T15:15:05')
    signal = make_signal(engine, 'EICHERMOT')
    engine.ingest(signal)
    engine.step()
    order, = rows(engine, Order)
    next_open = calendar.next_continuous_open('EICHERMOT', clock())
    assert order.expires_ms == next_open + 60_000
    assert order.reason == 'WAITING_NEXT_SESSION'
    assert engine.portfolio()['reserved_paise'] == 6_000_000
    at(clock, '2026-11-17T15:28:11')
    tick(engine, 'EICHERMOT')  # Even an apparent auction depth cannot fill.
    engine.step()
    assert rows(engine, Fill) == []
    recovered = PaperEngine(engine.db, engine.settings, calendar, clock)
    clock.value = next_open - 1
    tick(recovered, 'EICHERMOT')
    recovered.step()
    clock.value = next_open
    recovered.step()  # Reject yesterday/pre-open snapshots, even within max age.
    assert rows(engine, Fill) == []
    clock.advance(1000)
    tick(recovered, 'EICHERMOT')
    recovered.step()
    assert rows(engine, Order)[0].status == 'FILLED'
    assert rows(engine, Event)[0].reason is None
    assert 'wait_until_ms' not in rows(engine, Event)[0].decision
    assert engine.portfolio()['reserved_paise'] == 0


def test_non_cas_stock_still_fills_at_1515(system):
    engine, clock, _ = system
    real_calendar(engine)
    at(clock, '2026-11-17T15:15:05')
    engine.ingest(make_signal(engine, 'APARINDS'))
    engine.step()
    clock.advance()
    tick(engine, 'APARINDS')
    engine.step()
    assert rows(engine, Order)[0].status == 'FILLED'


def test_near_close_window_pauses_and_does_not_restart_its_used_seconds(system):
    engine, clock, _ = system
    calendar = real_calendar(engine)
    at(clock, '2026-11-17T15:14:40')
    engine.ingest(make_signal(engine, 'EICHERMOT'))
    engine.step()
    next_open = calendar.next_continuous_open('EICHERMOT', ms('2026-11-17T15:15:00'))
    assert rows(engine, Order)[0].expires_ms == next_open + 40_000
    at(clock, '2026-11-17T15:15:00')
    engine.step()
    assert rows(engine, Order)[0].reason == 'WAITING_NEXT_SESSION'
    clock.value = next_open + 39_000
    engine.step()
    assert rows(engine, Order)[0].status == 'PENDING'
    clock.advance(1000)
    engine.step()
    assert rows(engine, Order)[0].status == 'EXPIRED'
    assert engine.portfolio()['reserved_paise'] == 0


def test_partial_entry_exit_cancels_reserved_remainder_and_waits_overnight(system):
    engine, clock, _ = system
    calendar = real_calendar(engine)
    at(clock, '2026-11-17T15:14:50')
    entry = make_signal(engine, 'EICHERMOT')
    engine.ingest(entry)
    engine.step()
    clock.advance()
    tick(engine, 'EICHERMOT', size=5)
    engine.step()
    assert rows(engine, Order)[0].status == 'PARTIALLY_FILLED'
    at(clock, '2026-11-17T15:15:05')
    engine.ingest(make_signal(engine, 'EICHERMOT', action='EXIT_LONG', cycle=entry.trade_cycle_id))
    engine.step()
    buy, sell = rows(engine, Order)
    assert buy.status == 'CANCELLED' and buy.reason == 'EXIT_RECEIVED'
    assert sell.status == 'PENDING' and sell.reason == 'WAITING_NEXT_SESSION'
    assert sell.expires_ms is None
    assert engine.portfolio()['reserved_paise'] == 0
    clock.value = calendar.next_continuous_open('EICHERMOT', clock()) + 1000
    tick(engine, 'EICHERMOT')
    engine.settings.admin_token = SecretStr('a' * 40)
    engine.pause(True)  # Pausing entries must not prevent an exit resuming.
    engine.step()
    assert rows(engine, Order)[1].status == 'FILLED'
    assert engine.portfolio()['positions'] == []


def test_waiting_buy_is_cancelled_if_matching_exit_arrives_before_open(system):
    engine, clock, _ = system
    real_calendar(engine)
    at(clock, '2026-11-17T15:15:05')
    entry = make_signal(engine, 'EICHERMOT')
    engine.ingest(entry)
    engine.step()
    clock.advance()
    engine.ingest(make_signal(engine, 'EICHERMOT', action='EXIT_LONG', cycle=entry.trade_cycle_id))
    engine.step()
    assert rows(engine, Order)[0].reason == 'EXIT_RECEIVED'
    assert engine.portfolio()['reserved_paise'] == 0
    at(clock, '2026-11-18T09:15:01')
    tick(engine, 'EICHERMOT')
    engine.step()
    assert engine.portfolio()['positions'] == []


def test_closed_receipts_still_reserve_fifo_and_skip_ninth_for_funds(system):
    engine, clock, _ = system
    real_calendar(engine)
    at(clock, '2026-11-17T15:31:00')
    for symbol in SYMBOLS[:9]:
        engine.ingest(make_signal(engine, symbol))
    engine.step()
    orders = rows(engine, Order)
    assert [o.symbol for o in orders] == list(SYMBOLS[:8])
    assert all(o.reason == 'WAITING_NEXT_SESSION' for o in orders)
    assert rows(engine, Event)[-1].reason == 'INSUFFICIENT_FUNDS'
    assert engine.portfolio()['reserved_paise'] == 48_000_000


def test_fresh_closed_receipt_can_recover_at_next_open_but_old_alert_cannot(system):
    engine, clock, _ = system
    real_calendar(engine)
    at(clock, '2026-11-17T15:15:05')
    fresh = make_signal(engine, 'EICHERMOT')
    engine.ingest(fresh)
    at(clock, '2026-11-18T09:15:10')  # Durable inbox/worker downtime.
    engine.step()
    assert rows(engine, Event)[0].status == 'ACCEPTED'
    assert rows(engine, Order)[0].expires_ms == ms('2026-11-18T09:16:00')
    # A newly delivered yesterday signal must not gain a new next-session timer.
    late = fresh.model_copy(update={'symbol':'SBIN', 'ticker_id':'NSE:SBIN',
        'strategy_instance_id':fresh.strategy_instance_id.replace('EICHERMOT','SBIN'),
        'event_id':fresh.event_id.replace('EICHERMOT','SBIN'),
        'trade_cycle_id':fresh.trade_cycle_id.replace('EICHERMOT','SBIN')})
    engine.ingest(late)
    engine.step()
    assert rows(engine, Event)[1].reason == 'STALE_ENTRY'


def test_expired_intraday_entry_never_reopens_at_next_session(system):
    engine, clock, _ = system
    real_calendar(engine)
    at(clock, '2026-11-17T13:15:05')
    engine.ingest(make_signal(engine, 'EICHERMOT'))
    engine.step()
    at(clock, '2026-11-17T15:15:05')
    engine.step()
    assert rows(engine, Order)[0].status == 'EXPIRED'
    at(clock, '2026-11-18T09:15:01')
    tick(engine, 'EICHERMOT')
    engine.step()
    assert rows(engine, Fill) == []


def test_next_open_gap_protection_still_expires_without_chasing(system):
    engine, clock, _ = system
    calendar = real_calendar(engine)
    at(clock, '2026-11-17T15:15:05')
    engine.ingest(make_signal(engine, 'EICHERMOT'))
    engine.step()
    clock.value = calendar.next_continuous_open('EICHERMOT', clock()) + 1000
    tick(engine, 'EICHERMOT', bid='1100', ask='1101')
    engine.step()
    assert rows(engine, Fill) == []
    clock.advance(60_000)
    engine.step()
    assert rows(engine, Order)[0].status == 'EXPIRED'
    assert engine.portfolio()['reserved_paise'] == 0


def test_session_wait_notifications_are_deduplicated_and_readiness_is_explicit(system):
    engine, clock, _ = system
    real_calendar(engine)
    engine.settings.telegram_enabled = True
    engine.settings.telegram_bot_token = SecretStr('123:test')
    engine.settings.telegram_chat_id = SecretStr('123')
    at(clock, '2026-11-17T15:15:05')
    engine.ingest(make_signal(engine, 'EICHERMOT'))
    engine.step()
    for _ in range(3):
        clock.advance()
        engine.step()
    assert report(engine)['waiting_session_orders'] == 1
    assert len(rows(engine, Notification)) == 1
    assert '18 Nov 2026 09:15:00 IST' in rows(engine, Notification)[0].body
    at(clock, '2026-11-18T09:15:01')
    for _ in range(3):
        engine.step()
    assert report(engine)['waiting_session_orders'] == 0
    assert len(rows(engine, Notification)) == 2
