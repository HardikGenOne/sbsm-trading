"""Dhan web-token renewal. Network I/O never holds the financial account lock.

A committed RENEWING lease prevents duplicate calls. An abandoned/ambiguous
renewal requires reconnect: the broker may already have invalidated the token.
"""
from datetime import datetime
from hashlib import sha256
import time
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
import httpx
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import BrokerCredential, NotificationState
from .notifications import enqueue, ist_stamp

IST = ZoneInfo("Asia/Kolkata")


def fingerprint(value):
    return sha256(value.encode()).hexdigest()


def expiry_ms(value):
    if not isinstance(value, str):
        raise ValueError("Missing expiry")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        stamp = datetime.strptime(value, "%d/%m/%Y %H:%M")
    return int((stamp if stamp.tzinfo else stamp.replace(tzinfo=IST)).timestamp() * 1000)


def effective_settings(settings, db):
    """Load the current token for each consumer; never fall back to a stale seed."""
    if not settings.dhan_auto_renew:
        return settings
    token = ""
    with db.read() as session:
        row = session.get(BrokerCredential, 1)
        if (row and row.client_fingerprint == fingerprint(settings.dhan_client_id.get_secret_value())
                and row.status in ("READY", "RENEWING") and row.expires_ms > int(time.time() * 1000)):
            token = Fernet(settings.broker_token_key.get_secret_value().encode()).decrypt(row.encrypted_token.encode()).decode()
    return settings.model_copy(update={"dhan_access_token": SecretStr(token)})


def health(settings, db, now):
    if not settings.dhan_auto_renew:
        return {"enabled": False, "status": "WAITING", "message": "Automatic renewal is off; update the Dhan web token manually."}
    with db.read() as session:
        row = session.get(BrokerCredential, 1)
        if row is None:
            return {"enabled": True, "status": "WAITING", "message": "Waiting for the worker to verify the initial Dhan web token."}
        ready = row.status == "READY" and row.expires_ms > now
        message = (f"Automatic renewal enabled. Token valid until {ist_stamp(row.expires_ms)}."
                   if ready else f"Dhan renewal: {row.status}. {row.reason}. Reconnect with a fresh Dhan Web token if required.")
        return {"enabled": True, "status": "PASS" if ready else "WAITING" if row.status in ("UNVERIFIED", "CHECKING", "RENEWING") else "BLOCKED",
                "message": message, "expires_ms": row.expires_ms, "renewed_ms": row.renewed_ms,
                "generation": row.generation, "renewal_state": row.status}


class DhanTokenManager:
    def __init__(self, db, settings, clock, client=None):
        self.db, self.settings, self.clock = db, settings, clock
        self.client = client or httpx.Client(base_url="https://api.dhan.co", timeout=8, follow_redirects=False)
        self.cipher = Fernet(settings.broker_token_key.get_secret_value().encode()) if settings.dhan_auto_renew else None
        self.next_check = 0

    def close(self):
        self.client.close()

    def _claim(self, row, state, now):
        # Compare-and-swap also serializes across workers; release DB before HTTP.
        with Session(self.db.engine) as s, s.begin():
            return s.execute(update(BrokerCredential).where(
                BrokerCredential.id == 1, BrokerCredential.generation == row.generation,
                BrokerCredential.status == row.status, BrokerCredential.lease_until_ms <= now,
            ).values(status=state, lease_until_ms=now + 60_000)).rowcount == 1

    def _finish(self, expected_generation, state, **values):
        with Session(self.db.engine) as s, s.begin():
            return s.execute(update(BrokerCredential).where(
                BrokerCredential.id == 1, BrokerCredential.generation == expected_generation,
                BrokerCredential.status == state,
            ).values(lease_until_ms=0, **values)).rowcount == 1

    def _fail(self, generation, state, reason):
        now = self.clock()
        with Session(self.db.engine) as s, s.begin():
            changed = s.execute(update(BrokerCredential).where(
                BrokerCredential.id == 1, BrokerCredential.generation == generation,
                BrokerCredential.status == state,
            ).values(status="NEEDS_RECONNECT", reason=reason, lease_until_ms=0)).rowcount
            if changed:
                enqueue(s, self.settings, f"dhan-auth:{generation}:{reason}", "DHAN RECONNECT REQUIRED",
                        ["Automatic renewal could not safely continue.", f"Reason: {reason}",
                         "Generate a fresh Dhan Web access token, update SBSM_DHAN_ACCESS_TOKEN in Render and redeploy. Never paste tokens into chat."], now)

    def _profile(self, token):
        response = self.client.get("/v2/profile", headers={"access-token": token})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid profile")
        if str(payload.get("dhanClientId")) != self.settings.dhan_client_id.get_secret_value():
            raise ValueError("Account mismatch")
        expiry = expiry_ms(payload.get("tokenValidity"))
        if not self.clock() < expiry <= self.clock() + 26 * 3600_000:
            raise ValueError("Invalid expiry")
        return expiry

    def tick(self, force=False):
        now = self.clock()
        if not self.settings.dhan_auto_renew or (not force and now < self.next_check):
            return
        self.next_check = now + 60_000
        seed = self.settings.dhan_access_token.get_secret_value()
        client_id = self.settings.dhan_client_id.get_secret_value()
        if not seed or not client_id:
            return
        seed_fp = fingerprint(client_id + ":" + seed)
        with self.db.read() as s:
            row = s.get(BrokerCredential, 1)
            if row:
                s.expunge(row)
        if row is None:
            try:
                with Session(self.db.engine) as s, s.begin():
                    s.add(BrokerCredential(id=1, encrypted_token=self.cipher.encrypt(seed.encode()).decode(),
                          seed_fingerprint=seed_fp, client_fingerprint=fingerprint(client_id), generation=1,
                          expires_ms=0, renewed_ms=0, status="UNVERIFIED", lease_until_ms=0, next_attempt_ms=0, reason=""))
            except IntegrityError:
                return  # Another worker initialized it.
            return self.tick(force=True)
        # A changed Render seed is an explicit operator reconnect. Verify before
        # replacing the shared token; an old unchanged seed cannot undo renewal.
        if row.seed_fingerprint != seed_fp and row.lease_until_ms <= now:
            try:
                expires = self._profile(seed)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return
            if row.expires_ms and expires <= row.expires_ms:
                return  # An older process/seed must not replace a newer token.
            with Session(self.db.engine) as s, s.begin():
                s.execute(update(BrokerCredential).where(
                    BrokerCredential.id == 1, BrokerCredential.generation == row.generation,
                    BrokerCredential.lease_until_ms <= now,
                ).values(encrypted_token=self.cipher.encrypt(seed.encode()).decode(), seed_fingerprint=seed_fp,
                         client_fingerprint=fingerprint(client_id), generation=row.generation + 1, expires_ms=expires,
                         renewed_ms=0, status="READY", lease_until_ms=0, next_attempt_ms=0, reason=""))
            return
        if row.client_fingerprint != fingerprint(client_id):
            return
        if row.status == "RENEWING":
            if row.lease_until_ms <= now:
                self._fail(row.generation, "RENEWING", "RENEWAL_INTERRUPTED")
            return
        if row.status == "CHECKING" and row.lease_until_ms <= now:
            self._finish(row.generation, "CHECKING", status="UNVERIFIED")
            return
        if row.status == "NEEDS_RECONNECT" or row.next_attempt_ms > now:
            return
        if row.status == "UNVERIFIED" and self._claim(row, "CHECKING", now):
            token = self.cipher.decrypt(row.encrypted_token.encode()).decode()
            try:
                expires = self._profile(token)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    self._fail(row.generation, "CHECKING", "TOKEN_REJECTED")
                else:
                    self._finish(row.generation, "CHECKING", status="UNVERIFIED", next_attempt_ms=now + 300_000)
                return
            except (ValueError, KeyError, TypeError):
                self._fail(row.generation, "CHECKING", "INVALID_OR_EXPIRED_PROFILE")
                return
            except httpx.HTTPError:
                self._finish(row.generation, "CHECKING", status="UNVERIFIED", next_attempt_ms=now + 300_000)
                return
            self._finish(row.generation, "CHECKING", status="READY", expires_ms=expires, reason="", next_attempt_ms=0)
            return
        if row.status != "READY":
            return
        if row.expires_ms <= now:
            self._fail(row.generation, "READY", "TOKEN_EXPIRED")
            return
        if not force and row.expires_ms - now > self.settings.dhan_renew_before_hours * 3600_000:
            return
        if not self._claim(row, "RENEWING", now):
            return
        token = self.cipher.decrypt(row.encrypted_token.encode()).decode()
        try:
            response = self.client.get("/v2/RenewToken", headers={"access-token": token, "dhanClientId": client_id})
        except httpx.HTTPError:
            self._fail(row.generation, "RENEWING", "RENEWAL_DELIVERY_UNCERTAIN")
            return
        if response.status_code == 429:
            self._finish(row.generation, "RENEWING", status="READY", next_attempt_ms=now + 300_000, reason="RATE_LIMITED")
            return
        if response.status_code != 200:
            self._fail(row.generation, "RENEWING", "RENEWAL_REJECTED" if response.status_code < 500 else "RENEWAL_DELIVERY_UNCERTAIN")
            return
        # Preserve the complete broker response encrypted BEFORE interpreting it.
        # If Dhan changes its response shape, the only replacement is recoverable.
        recovery = {"generation": row.generation, "received_ms": now,
                    "http_status": response.status_code,
                    "ciphertext": self.cipher.encrypt(response.content).decode()}
        for attempt in range(3):
            try:
                with Session(self.db.engine) as s, s.begin():
                    s.merge(NotificationState(key="dhan_renewal_recovery", value=recovery))
                break
            except Exception:
                if attempt == 2:
                    raise RuntimeError("Dhan renewal recovery persistence failed") from None
                time.sleep(0.2)
        try:
            payload = response.json()
            # The documented form is top-level accessToken. Accept common
            # envelope/name variants only provisionally: /profile must verify
            # the candidate's account and expiry before any quote uses it.
            roots = [payload]
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                roots.append(payload["data"])
            replacement = None
            for item in roots:
                if not isinstance(item, dict):
                    continue
                account = item.get("dhanClientId")
                if account is not None and str(account) != client_id:
                    continue
                for name in ("accessToken", "access_token", "token"):
                    candidate = item.get(name)
                    if isinstance(candidate, str) and len(candidate) >= 20 and candidate != token:
                        replacement = candidate
                        break
                if replacement:
                    break
            if not replacement:
                raise ValueError("No replacement token")
        except (ValueError, KeyError, TypeError):
            self._fail(row.generation, "RENEWING", "INVALID_RENEWAL_RESPONSE")
            return
        # Save returned token BEFORE profile verification. A transient profile
        # failure must never lose the only valid replacement. No renewal retry.
        encrypted = self.cipher.encrypt(replacement.encode()).decode()
        for attempt in range(3):
            try:
                self._finish(row.generation, "RENEWING", encrypted_token=encrypted,
                             generation=row.generation + 1, status="UNVERIFIED", expires_ms=0,
                             renewed_ms=now, next_attempt_ms=0, reason="")
                break
            except Exception:
                if attempt == 2:
                    raise RuntimeError("Dhan replacement persistence failed; reconnect may be required") from None
                time.sleep(0.2)
        self.tick(force=True)  # Safe read-only profile validation, not renewal.
