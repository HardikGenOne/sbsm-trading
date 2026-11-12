"""Recover durable Vercel receipts through the existing idempotent signal engine."""
from sqlalchemy import select

from .engine import RateLimited, SignalConflict
from .models import WebhookReceipt
from .notifications import enqueue
from .schemas import Signal


def drain_inbox(engine, limit=100):
    with engine.db.read() as session:
        receipts = session.scalars(select(WebhookReceipt).where(
            WebhookReceipt.status == "RECEIVED",
        ).order_by(WebhookReceipt.id).limit(limit)).all()
    for receipt in receipts:
        # The worker can crash after ingest commits. A retry uses the same event ID
        # and the engine's existing duplicate/conflict handling.
        if receipt.kind == "test":
            with engine.db.write() as (session, _):
                current = session.get(WebhookReceipt, receipt.id)
                if current.status != "RECEIVED":
                    continue
                enqueue(session, engine.settings, "tv-test:" + receipt.payload["test_id"],
                        "TRADINGVIEW TEST", ["Durable webhook received.", f"Receipt #{receipt.id}"], engine.clock())
                current.status = "PROCESSED"
                current.result = {"test_id": receipt.payload["test_id"]}
            continue
        try:
            signal = Signal.model_validate(receipt.payload)
            result = engine.ingest(signal, received_ms=receipt.received_ms)
            status = "PROCESSED"
        except RateLimited:
            break
        except SignalConflict:
            result, status = {"reason": "EVENT_ID_CONFLICT"}, "REJECTED"
        except ValueError as exc:
            reason = {
                "Unapproved strategy configuration": "UNAPPROVED_STRATEGY_CONFIG",
                "Signal timestamp is in the future": "FUTURE_SIGNAL_TIMESTAMP",
            }.get(str(exc), "INVALID_SIGNAL_CONTRACT_OR_CONFIG")
            result, status = {"reason": reason}, "REJECTED"
        with engine.db.write() as (session, _):
            current = session.get(WebhookReceipt, receipt.id)
            if current.status != "RECEIVED":
                continue
            current.status, current.result = status, result
            if status == "REJECTED":
                enqueue(session, engine.settings, f"receipt:{receipt.id}:rejected",
                        "WEBHOOK REJECTED", [f"Receipt #{receipt.id}", result["reason"]], engine.clock())
