from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from sbsm.config import SYMBOLS
from sbsm.database import Database
from sbsm.engine import PaperEngine, SignalConflict
from sbsm.models import Account, Conflict, Event, Fill, Ledger, Order, Position
from sbsm.schemas import Signal
from conftest import enter, make_signal, tick


def all_rows(engine, model):
    with engine.db.read() as s:
        return s.scalars(select(model).order_by(model.id)).all()


def test_fifo_eight_reservations_and_ninth_skipped(system):
    engine, _, _ = system
    for symbol in SYMBOLS[:9]:
        engine.ingest(make_signal(engine, symbol))
    engine.step()
    events = all_rows(engine, Event)
    assert [e.status for e in events] == ["ACCEPTED"] * 8 + ["SKIPPED"]
    assert events[-1].reason == "INSUFFICIENT_FUNDS"
    assert events[-1].decision["required_paise"] == 6_000_000
    p = engine.portfolio()
    assert p["reserved_paise"] == 48_000_000
    assert p["available_paise"] == 2_000_000
    assert p["next_allocation_paise"] == 6_000_000


def test_concurrent_ingestion_and_workers_cannot_overspend(system):
    engine, _, _ = system
    signals = [make_signal(engine, symbol) for symbol in SYMBOLS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(engine.ingest, signals))
        list(pool.map(lambda _: engine.step(), range(4)))
    events = all_rows(engine, Event)
    accepted = [e for e in events if e.status == "ACCEPTED"]
    assert len(accepted) == 8
    assert [e.id for e in accepted] == [e.id for e in events[:8]]
    assert engine.portfolio()["available_paise"] == 2_000_000


def test_duplicate_and_conflicting_delivery(system):
    engine, _, _ = system
    signal = make_signal(engine)
    first = engine.ingest(signal)
    duplicate = signal.model_copy(update={"emitted_at_ms": signal.emitted_at_ms + 100})
    assert engine.ingest(duplicate)["sequence"] == first["sequence"]
    assert engine.ingest(duplicate)["duplicate"]
    body = signal.model_dump()
    body["reference_price"] = 1001
    with pytest.raises(SignalConflict):
        engine.ingest(Signal.model_validate(body))
    assert len(all_rows(engine, Conflict)) == 1
    assert len(all_rows(engine, Event)) == 1


def test_roundtrip_net_pnl_and_dynamic_allocation(system):
    engine, clock, _ = system
    entry = enter(engine, clock)
    p = engine.portfolio()
    assert len(p["positions"]) == 1
    position = p["positions"][0]
    # Initial entry fees stay in cost basis until shares are sold.
    assert p["pool_paise"] == 50_000_000
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id, price="1100"))
    engine.step()
    clock.advance()
    tick(engine, bid="1100", ask="1101")
    engine.step()
    p = engine.portfolio()
    fills = all_rows(engine, Fill)
    buy, sell = fills
    expected = sell.quantity * sell.price - sell.fees - (buy.quantity * buy.price + buy.fees)
    assert expected > 0
    assert p["realized_net_paise"] == expected
    assert p["pool_paise"] == p["cash_paise"] == 50_000_000 + expected
    assert p["next_allocation_paise"] == p["pool_paise"] * 1200 // 10000
    assert p["positions"] == []
    assert position["cost_paise"] == buy.quantity * buy.price + buy.fees
    assert p["reserved_paise"] == 0


def test_unrealized_profit_does_not_increase_pool(system):
    engine, clock, _ = system
    enter(engine, clock)
    clock.advance()
    tick(engine, bid="1500", ask="1501")
    p = engine.portfolio()
    assert p["positions"][0]["unrealized_paise"] > 0
    assert p["pool_paise"] == 50_000_000
    assert p["next_allocation_paise"] == 6_000_000


def test_partial_entry_exit_cancels_remainder_and_closes_only_fills(system):
    engine, clock, _ = system
    entry = make_signal(engine)
    engine.ingest(entry)
    engine.step()
    clock.advance()
    tick(engine, size=3)
    engine.step()
    assert engine.portfolio()["positions"][0]["quantity"] == 3
    # Reprocessing the same market snapshot cannot invent more liquidity.
    engine.step()
    assert engine.portfolio()["positions"][0]["quantity"] == 3
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.step()
    assert engine.portfolio()["reserved_paise"] == 0
    clock.advance()
    tick(engine, bid="999", ask="1000", size=2)
    engine.step()
    assert engine.portfolio()["positions"][0]["quantity"] == 1
    clock.advance()
    tick(engine, bid="999", ask="1000", size=100)
    engine.step()
    assert engine.portfolio()["positions"] == []
    buy = all_rows(engine, Order)[0]
    assert buy.status == "CANCELLED" and buy.filled_quantity == 3
    assert sum(f.quantity for f in all_rows(engine, Fill) if f.side == "SELL") == 3


def test_skipped_entry_exit_never_opens_short(system):
    engine, clock, _ = system
    engine.pause(True)
    entry = make_signal(engine)
    engine.ingest(entry)
    engine.step()
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.step()
    assert all_rows(engine, Order) == []
    assert all_rows(engine, Event)[-1].reason == "NO_MATCHING_POSITION"


def test_entry_and_exit_queued_during_downtime_do_not_open_late(system):
    engine, clock, _ = system
    entry = make_signal(engine)
    engine.ingest(entry)
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.step()
    assert all_rows(engine, Order) == []
    assert all_rows(engine, Event)[0].reason == "EXIT_ALREADY_RECEIVED"


def test_restart_recovers_pending_and_never_replays_skips(system):
    engine, clock, calendar = system
    for symbol in SYMBOLS[:9]:
        engine.ingest(make_signal(engine, symbol))
    engine.step()
    new_db = Database(engine.settings.database_url)
    new_db.initialize(99, clock())  # Existing account is not reset from environment.
    recovered = PaperEngine(new_db, engine.settings, calendar, clock)
    clock.advance()
    tick(recovered, SYMBOLS[0])
    recovered.step()
    assert len(recovered.portfolio()["positions"]) == 1
    assert all_rows(recovered, Event)[-1].status == "SKIPPED"
    assert recovered.portfolio()["initial_paise"] == 50_000_000
    new_db.engine.dispose()


def test_closed_market_entries_skipped_exits_retained_overnight(system):
    engine, clock, calendar = system
    entry = enter(engine, clock)
    calendar.opened = False
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.ingest(make_signal(engine, symbol="SBIN"))
    engine.step()
    assert all_rows(engine, Event)[-1].reason == "MARKET_CLOSED"
    assert all_rows(engine, Order)[-1].side == "SELL"
    assert all_rows(engine, Order)[-1].status == "PENDING"
    clock.advance(86_400_000)
    calendar.opened = True
    tick(engine)
    engine.step()
    assert engine.portfolio()["positions"] == []


def test_expiration_releases_reserve_without_quote_fill(system):
    engine, clock, _ = system
    engine.ingest(make_signal(engine))
    engine.step()
    clock.advance(61_000)
    engine.step()
    assert engine.portfolio()["reserved_paise"] == 0
    assert engine.portfolio()["positions"] == []
    assert all_rows(engine, Order)[0].status == "EXPIRED"


def test_stale_quote_and_pre_latency_quote_cannot_fill(system):
    engine, clock, _ = system
    engine.settings.execution_latency_ms = 500
    engine.ingest(make_signal(engine))
    engine.step()
    tick(engine)
    clock.advance(600)
    engine.step()
    assert engine.portfolio()["positions"] == []
    clock.advance(12_000)
    engine.step()
    assert engine.portfolio()["positions"] == []
    tick(engine)
    engine.step()
    assert len(engine.portfolio()["positions"]) == 1


def test_second_entry_same_stock_and_unmatched_exit_are_safe(system):
    engine, clock, _ = system
    enter(engine, clock)
    clock.advance()
    second = make_signal(engine)
    engine.ingest(second)
    engine.step()
    assert all_rows(engine, Event)[-1].reason == "POSITION_OR_ENTRY_EXISTS"
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=second.trade_cycle_id))
    engine.step()
    assert engine.portfolio()["reconciliation_required"]
    assert len(engine.portfolio()["positions"]) == 1
    assert all_rows(engine, Event)[-1].reason == "UNMATCHED_EXIT_WITH_OPEN_POSITION"


def test_pause_does_not_block_exits(system):
    engine, clock, _ = system
    entry = enter(engine, clock)
    engine.pause(True)
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.step()
    clock.advance()
    tick(engine)
    engine.step()
    assert engine.portfolio()["positions"] == []


def test_settlement_hold_and_release(system):
    engine, clock, _ = system
    engine.settings.settlement_mode = "next_session"
    entry = enter(engine, clock)
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.step()
    clock.advance()
    tick(engine)
    engine.step()
    p = engine.portfolio()
    assert p["unsettled_paise"] > 0
    assert p["available_paise"] == p["cash_paise"] - p["unsettled_paise"]
    clock.advance(86_400_000)
    engine.step()
    assert engine.portfolio()["unsettled_paise"] == 0


def test_ledger_reconstructs_all_balances(system):
    engine, clock, _ = system
    enter(engine, clock)
    p = engine.portfolio()
    ledger = all_rows(engine, Ledger)
    assert sum(row.cash_delta for row in ledger) == p["cash_paise"]
    assert sum(row.reserve_delta for row in ledger) == p["reserved_paise"]
    assert sum(row.realized_delta for row in ledger) == p["realized_net_paise"]


def test_loss_reduces_future_allocation(system):
    engine, clock, _ = system
    entry = enter(engine, clock)
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.step()
    clock.advance()
    tick(engine, bid="900", ask="901")
    engine.step()
    p = engine.portfolio()
    assert p["realized_net_paise"] < 0
    assert p["next_allocation_paise"] < 6_000_000
    assert p["pool_paise"] == p["cash_paise"]


def test_protected_price_waits_instead_of_chasing(system):
    engine, clock, _ = system
    engine.ingest(make_signal(engine))
    engine.step()
    clock.advance()
    tick(engine, bid="1100", ask="1101")
    engine.step()
    assert engine.portfolio()["positions"] == []
    assert engine.portfolio()["reserved_paise"] == 6_000_000
    clock.advance(60_000)
    engine.step()
    assert engine.portfolio()["reserved_paise"] == 0


def test_unaffordable_share_releases_budget(system):
    engine, clock, _ = system
    engine.ingest(make_signal(engine, price="100000"))
    engine.step()
    clock.advance()
    tick(engine, bid="100000", ask="100000")
    engine.step()
    assert all_rows(engine, Event)[0].reason == "BUDGET_BELOW_ONE_SHARE"
    assert engine.portfolio()["reserved_paise"] == 0


def test_operator_close_is_durable_and_deduplicated(system):
    engine, clock, _ = system
    entry = enter(engine, clock)
    first = engine.request_close(entry.trade_cycle_id)
    second = engine.request_close(entry.trade_cycle_id)
    assert first["sequence"] == second["sequence"]
    assert second["already_pending"]
    engine.step()
    clock.advance()
    tick(engine)
    engine.step()
    assert engine.portfolio()["positions"] == []
