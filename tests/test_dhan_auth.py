import time
from datetime import datetime
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sbsm.dhan_auth import DhanTokenManager, effective_settings, health
from sbsm.models import BrokerCredential, Ledger, Account, Notification

OLD = "old-private-token-" + "x" * 30
NEW = "new-private-token-" + "y" * 30


def setup_auth(system, responder=None):
    engine, clock, _ = system
    clock.value = int(time.time() * 1000)
    settings = engine.settings
    settings.dhan_auto_renew = True
    settings.broker_token_key = SecretStr(Fernet.generate_key().decode())
    settings.dhan_client_id = SecretStr("client")
    settings.dhan_access_token = SecretStr(OLD)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if responder:
            result = responder(request)
            if result is not None:
                return result
        if request.url.path == "/v2/profile":
            return httpx.Response(200, json={"dhanClientId": "client", "tokenValidity": datetime.fromtimestamp(
                (clock() + 24 * 3600_000) / 1000, ZoneInfo("Asia/Kolkata")).strftime("%d/%m/%Y %H:%M")})
        assert request.url.path == "/v2/RenewToken"
        assert request.headers["dhanClientId"] == "client"
        assert request.headers["access-token"] == OLD
        return httpx.Response(200, json={"accessToken": NEW, "dhanClientId": "client"})

    client = httpx.Client(base_url="https://api.dhan.co", transport=httpx.MockTransport(respond))
    return DhanTokenManager(engine.db, settings, clock, client), calls


def row(engine):
    with engine.db.read() as s:
        r = s.get(BrokerCredential, 1)
        if r is not None:
            s.expunge(r)
        return r


def financial_snapshot(engine):
    with engine.db.read() as s:
        a = s.get(Account, 1)
        return (a.cash, a.reserved, a.realized, a.unsettled, list(s.scalars(select(Ledger.id))))


def test_encrypted_rotation_is_shared_restart_safe_and_does_not_change_ledger(system):
    engine, clock, _ = system
    manager, calls = setup_auth(system)
    before = financial_snapshot(engine)
    assert not effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value()
    manager.tick()
    assert row(engine).status == "READY"
    assert OLD not in row(engine).encrypted_token
    manager.tick(force=True)
    assert row(engine).generation == 2
    assert row(engine).renewed_ms == clock()
    assert row(engine).status == "READY"
    assert NEW not in row(engine).encrypted_token
    assert effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value() == NEW
    # Restart uses the database replacement, never the still-old Render seed.
    manager.next_check = 0
    manager.tick()
    assert calls.count("/v2/RenewToken") == 1
    assert financial_snapshot(engine) == before
    assert "private-token" not in str(health(engine.settings, engine.db, clock()))


def test_due_window_renews_before_expiry_and_not_on_every_tick(system):
    engine, clock, _ = system
    manager, calls = setup_auth(system)
    manager.tick()
    clock.advance(15 * 3600_000)
    manager.tick()
    assert calls.count("/v2/RenewToken") == 0
    clock.advance(2 * 3600_000)
    manager.tick()
    assert calls.count("/v2/RenewToken") == 1
    clock.advance(60_000)
    manager.tick()
    assert calls.count("/v2/RenewToken") == 1


@pytest.mark.parametrize("kind", ["timeout", "bad_json", "server_error", "rejected", "wrong_account"])
def test_ambiguous_or_rejected_renewal_does_not_retry_and_notifies_once(system, kind):
    engine, clock, _ = system
    def responder(request):
        if request.url.path != "/v2/RenewToken":
            return None
        if kind == "timeout":
            raise httpx.ReadTimeout("private broker details", request=request)
        if kind == "bad_json":
            return httpx.Response(200, text="secret-malformed")
        if kind == "wrong_account":
            return httpx.Response(200, json={"accessToken": NEW, "dhanClientId": "other"})
        return httpx.Response(503 if kind == "server_error" else 401)
    manager, calls = setup_auth(system, responder)
    engine.settings.telegram_enabled = True
    engine.settings.telegram_bot_token = SecretStr("123:" + "z" * 30)
    engine.settings.telegram_chat_id = SecretStr("456")
    manager.tick()
    manager.tick(force=True)
    clock.advance(120_000)
    manager.tick(force=True)
    assert row(engine).status == "NEEDS_RECONNECT"
    assert calls.count("/v2/RenewToken") == 1
    assert effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value() == ""
    with engine.db.read() as s:
        notices = list(s.scalars(select(Notification)))
        assert len(notices) == 1
        assert OLD not in notices[0].body and NEW not in notices[0].body


def test_new_token_saved_even_when_followup_profile_times_out(system):
    engine, clock, _ = system
    def responder(request):
        if request.url.path == "/v2/profile" and request.headers.get("access-token") == NEW:
            raise httpx.ReadTimeout("secret", request=request)
    manager, calls = setup_auth(system, responder)
    manager.tick()
    manager.tick(force=True)
    saved = row(engine)
    assert saved.status == "UNVERIFIED" and saved.generation == 2
    cipher = Fernet(engine.settings.broker_token_key.get_secret_value().encode())
    assert cipher.decrypt(saved.encrypted_token.encode()).decode() == NEW
    assert calls.count("/v2/RenewToken") == 1


def test_second_worker_cannot_renew_while_first_is_in_flight(system):
    engine, clock, _ = system
    observed = []
    def responder(request):
        if request.url.path == "/v2/RenewToken":
            # Other worker sees committed lease. HTTP holds no financial lock.
            with engine.db.write() as (_, account):
                observed.append(account.cash)
            other.tick(force=True)
    manager, calls = setup_auth(system, responder)
    other = DhanTokenManager(engine.db, engine.settings, clock, manager.client)
    manager.tick()
    manager.tick(force=True)
    assert calls.count("/v2/RenewToken") == 1
    assert observed and row(engine).status == "READY"


def test_crashed_renewal_requires_reconnect_instead_of_blind_retry(system):
    engine, clock, _ = system
    manager, calls = setup_auth(system)
    manager.tick()
    with Session(engine.db.engine) as s, s.begin():
        r = s.get(BrokerCredential, 1)
        r.status = "RENEWING"
        r.lease_until_ms = clock() + 60_000
    clock.advance(61_000)
    manager.tick()
    assert row(engine).reason == "RENEWAL_INTERRUPTED"
    assert "/v2/RenewToken" not in calls


def test_expired_token_does_not_call_renew_endpoint(system):
    engine, clock, _ = system
    manager, calls = setup_auth(system)
    manager.tick()
    clock.advance(25 * 3600_000)
    manager.tick()
    assert row(engine).reason == "TOKEN_EXPIRED"
    assert "/v2/RenewToken" not in calls


def test_rate_limit_has_backoff_without_losing_current_token(system):
    engine, clock, _ = system
    manager, calls = setup_auth(system, lambda r: httpx.Response(429) if r.url.path.endswith("RenewToken") else None)
    manager.tick()
    manager.tick(force=True)
    assert row(engine).status == "READY"
    clock.advance(60_000)
    manager.tick(force=True)
    assert calls.count("/v2/RenewToken") == 1
    assert effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value() == OLD


def test_explicit_reconnect_accepts_fresh_seed_only(system):
    engine, clock, _ = system
    manager, calls = setup_auth(system)
    manager.tick()
    clock.advance(3600_000)
    engine.settings.dhan_access_token = SecretStr(NEW)
    manager.tick()
    assert effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value() == NEW
    assert row(engine).generation == 2
    engine.settings.dhan_access_token = SecretStr(OLD)
    # Same expiry as replacement: stale rolling process cannot undo reconnect.
    manager.tick(force=True)
    assert row(engine).generation == 2
    assert effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value() == NEW


def test_dashboard_quote_uses_rotated_token(system, monkeypatch):
    from fastapi.testclient import TestClient
    from sbsm.api import create_app
    engine, clock, calendar = system
    manager, _ = setup_auth(system)
    manager.tick()
    manager.tick(force=True)
    tokens = []
    class Feed:
        def fetch(self, symbols):
            return []
        def close(self):
            pass
    def quote_feed(settings, provider):
        tokens.append(settings.dhan_access_token.get_secret_value())
        return Feed()
    monkeypatch.setattr("sbsm.api.quote_feed", quote_feed)
    auth = {"Authorization": "Bearer " + "a" * 40}
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as client:
        response = client.get("/api/market-data/quote?provider=dhan&symbol=ICICIBANK", headers=auth)
        assert response.status_code == 200
        assert NEW not in response.text and OLD not in response.text
        assert tokens == [NEW]
        readiness = client.get("/api/readiness", headers=auth)
        assert NEW not in readiness.text and OLD not in readiness.text
        assert readiness.json()["dhan_token_renewal"]["generation"] == 2


def test_auto_renew_requires_valid_key():
    from sbsm.config import Settings
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="encryption key"):
        Settings(_env_file=None, admin_token="a" * 40, webhook_token="b" * 40, dhan_auto_renew=True)


def test_migration_from_existing_outbox_preserves_funds(system):
    from sbsm.migrations import VERSIONS, AUTH_REVISION
    from sqlalchemy import delete
    engine, clock, _ = system
    before = financial_snapshot(engine)
    with engine.db.engine.begin() as connection:
        BrokerCredential.__table__.drop(connection)
        connection.execute(delete(VERSIONS).where(VERSIONS.c.revision == AUTH_REVISION))
    engine.db.initialize(engine.settings.initial_capital_paise, clock())
    assert financial_snapshot(engine) == before
    assert row(engine) is None


@pytest.mark.parametrize("envelope", [False, True])
def test_renewal_response_variants_are_verified_and_recoverable(system, envelope):
    import json
    from sbsm.models import NotificationState
    engine, clock, _ = system
    payload = {"access_token": NEW}
    if envelope:
        payload = {"status": "success", "data": payload}
    manager, calls = setup_auth(system, lambda request: httpx.Response(200, json=payload)
                                if request.url.path.endswith("RenewToken") else None)
    manager.tick()
    manager.tick(force=True)
    assert row(engine).status == "READY"
    assert effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value() == NEW
    with engine.db.read() as s:
        saved = s.get(NotificationState, "dhan_renewal_recovery").value
    assert OLD not in str(saved) and NEW not in str(saved)
    cipher = Fernet(engine.settings.broker_token_key.get_secret_value().encode())
    assert json.loads(cipher.decrypt(saved["ciphertext"].encode())) == payload


def test_unknown_response_remains_encrypted_for_recovery(system):
    from sbsm.models import NotificationState
    engine, clock, _ = system
    manager, _ = setup_auth(system, lambda r: httpx.Response(200, text='{"futureShape":"'+NEW+'"}')
                            if r.url.path.endswith("RenewToken") else None)
    manager.tick()
    manager.tick(force=True)
    assert row(engine).reason == "INVALID_RENEWAL_RESPONSE"
    with engine.db.read() as s:
        saved = s.get(NotificationState, "dhan_renewal_recovery").value
    assert NEW not in str(saved)
    assert NEW in Fernet(engine.settings.broker_token_key.get_secret_value().encode()).decrypt(saved["ciphertext"].encode()).decode()


def test_observed_dhan_web_renewal_shape(system):
    """Live Dhan returns token/createTime/expiryTime, unlike the docs sample."""
    engine, clock, _ = system
    response = {"token": NEW, "createTime": "2026-11-23T09:27:00", "expiryTime": "2026-11-24T09:27:00"}
    manager, calls = setup_auth(system, lambda request: httpx.Response(200, json=response)
                                if request.url.path.endswith("RenewToken") else None)
    manager.tick()
    manager.tick(force=True)
    assert row(engine).generation == 2
    assert row(engine).status == "READY"
    assert effective_settings(engine.settings, engine.db).dhan_access_token.get_secret_value() == NEW
    assert calls.count("/v2/RenewToken") == 1
