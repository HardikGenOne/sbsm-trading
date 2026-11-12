from sqlalchemy import select, func

from conftest import make_signal
from sbsm.models import WebhookReceipt, Event, Notification, Ledger
from sbsm.receiver import drain_inbox


def receipt(engine, payload, key="a", kind="signal"):
    with engine.db.write() as (session, _):
        session.add(WebhookReceipt(receipt_key=key, received_ms=engine.clock(),
                                   payload=payload, kind=kind, status="RECEIVED", result={}))


def test_recovery_deduplicates_and_preserves_original_receipt_time(system):
    engine, clock, _ = system
    signal = make_signal(engine)
    receipt(engine, signal.model_dump(mode="json"))
    original = clock()
    clock.advance(180000)
    drain_inbox(engine)
    # Simulate a restart after ingest committed, before marking the receipt done.
    with engine.db.write() as (session, _):
        session.get(WebhookReceipt, 1).status = "RECEIVED"
    drain_inbox(engine)
    engine.step()
    with engine.db.read() as session:
        event = session.scalar(select(Event))
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert event.received_ms == original
        assert event.status == "SKIPPED"
        assert session.get(WebhookReceipt, 1).status == "PROCESSED"


def test_test_receipt_and_invalid_signal_leave_ledger_untouched(system):
    engine, _, _ = system
    receipt(engine, {"test_id": "sbs-test-001"}, kind="test")
    receipt(engine, {"action": "bad"}, key="b")
    drain_inbox(engine)
    drain_inbox(engine)
    with engine.db.read() as session:
        assert session.get(WebhookReceipt, 1).status == "PROCESSED"
        assert session.get(WebhookReceipt, 2).status == "REJECTED"
        assert session.scalar(select(func.count()).select_from(Event)) == 0
        assert session.scalar(select(func.count()).select_from(Ledger)) == 1


def legacy_pine_signal(signal):
    """Reproduce the original Pine helper's JSON-decoded carriage returns."""
    from sbsm.schemas import Signal
    payload = signal.model_dump()
    for key in ('config_key', 'strategy_instance_id', 'event_id', 'trade_cycle_id'):
        payload[key] = payload[key].replace('r', chr(13))
    return Signal.model_validate(payload)


def test_legacy_exit_receipt_becomes_visible_no_position_decision(system):
    engine, clock, _ = system
    signal = make_signal(engine)
    clock.advance()
    exit_signal = legacy_pine_signal(make_signal(
        engine, action='EXIT_LONG', cycle=signal.trade_cycle_id))
    receipt(engine, exit_signal.model_dump(mode='json'))
    drain_inbox(engine)
    engine.step()
    with engine.db.read() as session:
        saved = session.get(WebhookReceipt, 1)
        event = session.scalar(select(Event))
        assert saved.status == 'PROCESSED'
        assert saved.result['sequence'] == event.id
        assert event.event_id == exit_signal.event_id
        assert event.cycle_id == exit_signal.trade_cycle_id
        assert event.status == 'SKIPPED'
        assert event.reason == 'NO_MATCHING_POSITION'
        assert session.scalar(select(func.count()).select_from(Ledger)) == 1


def test_legacy_entry_and_exit_keep_cycle_and_duplicate_rules(system):
    from conftest import tick
    from sbsm.models import Position, Fill
    engine, clock, _ = system
    entry = make_signal(engine)
    legacy_entry = legacy_pine_signal(entry)
    engine.ingest(legacy_entry)
    assert engine.ingest(legacy_entry)['duplicate']
    engine.step()
    clock.advance()
    tick(engine)
    engine.step()
    with engine.db.read() as session:
        assert session.scalar(select(Position)).cycle_id == legacy_entry.trade_cycle_id
    clock.advance()
    engine.ingest(legacy_pine_signal(make_signal(
        engine, action='EXIT_LONG', cycle=entry.trade_cycle_id)))
    engine.step()
    clock.advance()
    tick(engine)
    engine.step()
    assert engine.portfolio()['positions'] == []
    with engine.db.read() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 2
        assert session.scalar(select(func.count()).select_from(Fill)) == 2


def test_legacy_spelling_does_not_admit_different_parameters(system):
    from sbsm.schemas import Signal
    engine, _, _ = system
    payload = legacy_pine_signal(make_signal(engine)).model_dump(mode='json')
    for key in ('config_key', 'strategy_instance_id', 'event_id', 'trade_cycle_id'):
        payload[key] = payload[key].replace('noise=25', 'noise=24')
    # Internally consistent IDs still must match the approved parameters.
    Signal.model_validate(payload)
    receipt(engine, payload)
    drain_inbox(engine)
    with engine.db.read() as session:
        assert session.get(WebhookReceipt, 1).result['reason'] == 'UNAPPROVED_STRATEGY_CONFIG'
        assert session.scalar(select(func.count()).select_from(Event)) == 0
