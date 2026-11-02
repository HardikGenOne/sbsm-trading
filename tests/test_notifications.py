import json

from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import select

from sbsm.api import create_app
from sbsm.database import Database
from sbsm.models import Account, Base, Notification
from sbsm.notifications import DeliveryError, Dispatcher, TelegramClient, enqueue
from sbsm.notifier import watch_worker
from conftest import enter, make_signal, tick


def enable(engine):
    engine.settings.telegram_enabled = True
    engine.settings.telegram_bot_token = SecretStr("12345678:" + "x" * 35)
    engine.settings.telegram_chat_id = SecretStr("123456789")


def queued(engine):
    with engine.db.read() as s:
        return s.scalars(select(Notification).order_by(Notification.id)).all()


class Sender:
    def __init__(self, error=None):
        self.error, self.sent = error, []

    def send(self, chat, body):
        self.sent.append((chat, body))
        if self.error:
            raise self.error
        return 42


def test_trade_changes_and_notifications_commit_together(system):
    engine, clock, _ = system
    enable(engine)
    entry = enter(engine, clock)
    assert len(queued(engine)) == 2
    assert "ENTRY ACCEPTED" in queued(engine)[0].body
    assert "BUY FILLED" in queued(engine)[1].body
    engine.ingest(entry)  # Delivery retries cannot create another message.
    engine.step()
    assert len(queued(engine)) == 2
    clock.advance()
    engine.ingest(make_signal(engine, action="EXIT_LONG", cycle=entry.trade_cycle_id))
    engine.step()
    clock.advance()
    tick(engine, bid="1100", ask="1101")
    engine.step()
    messages = queued(engine)
    assert len(messages) == 4
    assert "Net realized on this fill" in messages[-1].body
    assert "Next 12% allocation" in messages[-1].body
    assert engine.settings.telegram_bot_token.get_secret_value() not in "".join(row.body for row in messages)


def test_outbox_rolls_back_with_failed_financial_transaction(system, monkeypatch):
    engine, _, _ = system
    enable(engine)
    engine.ingest(make_signal(engine))
    original = engine.entry_intent
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Injected failure after reservation")
    monkeypatch.setattr(engine, "entry_intent", fail)
    with pytest.raises(RuntimeError):
        engine.step()
    assert queued(engine) == []
    assert engine.portfolio()["reserved_paise"] == 0


def test_disabled_notifications_create_no_backlog(system):
    engine, clock, _ = system
    enter(engine, clock)
    assert queued(engine) == []


def test_skip_message_explains_fund_decision(system):
    engine, _, _ = system
    enable(engine)
    engine.pause(True)
    engine.ingest(make_signal(engine))
    engine.step()
    body = queued(engine)[0].body
    assert "ENTRIES_PAUSED" in body and "Required:" in body and "Available:" in body


def test_dispatch_success_and_durable_restart(system):
    engine, clock, _ = system
    enable(engine)
    enter(engine, clock)
    sender = Sender()
    dispatcher = Dispatcher(engine.db, engine.settings, sender, clock)
    assert dispatcher.dispatch_once()
    assert queued(engine)[0].status == "SENT"
    assert queued(engine)[0].telegram_message_id == 42
    # New dispatcher sees only the remaining row, never the already-sent one.
    Dispatcher(engine.db, engine.settings, sender, clock).dispatch_once()
    assert len(sender.sent) == 2
    assert not dispatcher.dispatch_once()


def test_temporary_failure_retries_after_backoff(system):
    engine, clock, _ = system
    enable(engine)
    engine.ingest(make_signal(engine))
    engine.step()
    sender = Sender(DeliveryError("NETWORK_OR_TIMEOUT"))
    dispatcher = Dispatcher(engine.db, engine.settings, sender, clock)
    dispatcher.dispatch_once()
    assert queued(engine)[0].status == "PENDING"
    assert not dispatcher.dispatch_once()
    clock.advance(5000)
    sender.error = None
    assert dispatcher.dispatch_once()
    assert queued(engine)[0].status == "SENT"


def test_rate_limits_permanent_errors_and_changed_recipient(system):
    engine, clock, _ = system
    enable(engine)
    engine.ingest(make_signal(engine))
    engine.step()
    sender = Sender(DeliveryError("RATE_LIMITED", retry_after=60))
    dispatcher = Dispatcher(engine.db, engine.settings, sender, clock)
    dispatcher.dispatch_once()
    assert queued(engine)[0].due_ms == clock() + 60000
    clock.advance(60000)
    sender.error = DeliveryError("AUTH_OR_CHAT_FORBIDDEN", permanent=True)
    dispatcher.dispatch_once()
    assert queued(engine)[0].status == "FAILED"
    with engine.db.write() as (s, _):
        row = s.get(Notification, queued(engine)[0].id)
        row.status, row.due_ms = "PENDING", clock()
    engine.settings.telegram_chat_id = SecretStr("987654321")
    sender.sent.clear()
    dispatcher.dispatch_once()
    assert queued(engine)[0].status == "BLOCKED"
    assert sender.sent == []


def test_abandoned_delivery_lease_recovers(system):
    engine, clock, _ = system
    enable(engine)
    engine.ingest(make_signal(engine))
    engine.step()
    with engine.db.write() as (s, _):
        row = s.scalar(select(Notification))
        row.status, row.attempts, row.lease_until_ms = "SENDING", 1, clock() + 30000
    dispatcher = Dispatcher(engine.db, engine.settings, Sender(), clock)
    assert not dispatcher.dispatch_once()
    clock.advance(30001)
    assert dispatcher.dispatch_once()
    assert queued(engine)[0].status == "SENT"


def test_heartbeat_alerts_only_on_changes(system):
    engine, clock, _ = system
    enable(engine)
    engine.step()
    watch_worker(engine.db, engine.settings, clock)
    clock.advance(91000)
    watch_worker(engine.db, engine.settings, clock)
    watch_worker(engine.db, engine.settings, clock)
    assert len(queued(engine)) == 1
    engine.step()
    watch_worker(engine.db, engine.settings, clock)
    watch_worker(engine.db, engine.settings, clock)
    assert len(queued(engine)) == 2
    assert "RECOVERED" in queued(engine)[-1].body


def test_telegram_http_error_response_is_sanitized():
    token = "12345678:" + "x" * 35
    def transport(request):
        assert request.url.host == "api.telegram.org"
        assert json.loads(request.content)["chat_id"] == "123456789"
        return httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 31}, "description": token})
    client = TelegramClient(token, httpx.Client(transport=httpx.MockTransport(transport)))
    with pytest.raises(DeliveryError) as exc:
        client.send("123456789", "test")
    assert exc.value.retry_after == 31
    assert token not in str(exc.value)
    client.close()


def test_notification_api_hides_destination_and_restricts_test(system):
    engine, clock, calendar = system
    auth = {"Authorization": "Bearer " + "a" * 40}
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as client:
        assert client.get("/api/notifications").status_code == 401
        assert client.post("/controls/notifications/test", headers=auth).status_code == 409
        enable(engine)
        assert client.post("/controls/notifications/test", headers=auth).status_code == 202
        response = client.get("/api/notifications", headers=auth)
        assert response.json()["counts"]["PENDING"] == 1
        assert "123456789" not in response.text
        assert engine.settings.telegram_bot_token.get_secret_value() not in response.text


def test_additive_migration_preserves_existing_account(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'old.db'}")
    baseline = [table for table in Base.metadata.sorted_tables if table.name not in ("notifications", "notification_state")]
    Base.metadata.create_all(db.engine, tables=baseline)
    with db.write() as (s, _):
        s.add(Account(id=1, initial=50_000_000, cash=49_000_000, reserved=0, unsettled=0, realized=0, paused=False, reconciliation_required=False, worker_heartbeat_ms=0))
    db.initialize(123, 1)
    with db.read() as s:
        assert s.get(Account, 1).cash == 49_000_000
        assert s.scalar(select(Notification)) is None
    db.initialize(123, 2)  # Migration is idempotent.
    db.engine.dispose()


def test_delivery_failure_never_changes_trading_balances(system):
    engine, clock, _ = system
    enable(engine)
    enter(engine, clock)
    before = engine.portfolio()
    Dispatcher(engine.db, engine.settings, Sender(DeliveryError("NETWORK_OR_TIMEOUT")), clock).dispatch_once()
    assert engine.portfolio() == before


def test_retry_limit_is_terminal(system):
    engine, clock, _ = system
    enable(engine)
    engine.settings.telegram_max_attempts = 1
    engine.ingest(make_signal(engine))
    engine.step()
    dispatcher = Dispatcher(engine.db, engine.settings, Sender(DeliveryError("TELEGRAM_UNAVAILABLE")), clock)
    dispatcher.dispatch_once()
    assert queued(engine)[0].status == "FAILED"
    clock.advance(1_000_000)
    assert not dispatcher.dispatch_once()
