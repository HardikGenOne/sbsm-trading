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
