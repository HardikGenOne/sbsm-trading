"""Transactional Telegram outbox. Enqueuing never makes a network request."""
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import or_, select

from .models import Notification


def rupees(value):
    sign = "-" if value < 0 else ""
    return f"{sign}₹{abs(value) // 100:,}.{abs(value) % 100:02d}"


def enqueue(session, settings, key, title, lines, now):
    if not settings.telegram_enabled:
        return
    if session.scalar(select(Notification.id).where(Notification.dedupe_key == key)):
        return
    stamp = datetime.fromtimestamp(now / 1000, ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y %H:%M:%S IST")
    body = "\n".join([f"SBSM PAPER · {title}", *lines, f"Time: {stamp}", f"Ref: {key}"])
    if settings.public_base_url:
        body += "\n" + settings.public_base_url.rstrip("/")
    session.add(Notification(dedupe_key=key, created_ms=now, due_ms=now, status="PENDING", body=body[:4000], bot_id=settings.telegram_bot_token.get_secret_value().split(":", 1)[0], chat_id=settings.telegram_chat_id.get_secret_value(), attempts=0, lease_until_ms=0))


class DeliveryError(Exception):
    def __init__(self, code, retry_after=None, permanent=False):
        self.code, self.retry_after, self.permanent = code, retry_after, permanent
        super().__init__(code)  # Never include Telegram URLs, tokens or raw responses.


class TelegramClient:
    def __init__(self, token, client=None):
        self.token = token
        self.client = client or httpx.Client(timeout=8)

    def send(self, chat_id, text):
        try:
            response = self.client.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json={"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}})
        except httpx.HTTPError as exc:
            raise DeliveryError("NETWORK_OR_TIMEOUT") from None
        try:
            payload = response.json()
        except ValueError:
            raise DeliveryError("INVALID_RESPONSE") from None
        if not isinstance(payload, dict):
            raise DeliveryError("INVALID_RESPONSE")
        if response.is_success and payload.get("ok") is True:
            result = payload.get("result")
            if not isinstance(result, dict) or type(result.get("message_id")) is not int:
                raise DeliveryError("INVALID_RESPONSE")
            return result["message_id"]
        code = payload.get("error_code", response.status_code)
        if type(code) is not int:
            raise DeliveryError("INVALID_RESPONSE")
        if code == 429:
            delay = payload.get("parameters", {}).get("retry_after", 30)
            if type(delay) is not int:
                delay = 30
            raise DeliveryError("RATE_LIMITED", retry_after=max(1, min(86400, delay)))
        if 400 <= code < 500:
            raise DeliveryError("AUTH_OR_CHAT_FORBIDDEN" if code in (401, 403) else "REQUEST_REJECTED", permanent=True)
        raise DeliveryError("TELEGRAM_UNAVAILABLE")

    def close(self):
        self.client.close()


class Dispatcher:
    def __init__(self, database, settings, client, clock):
        self.db, self.settings, self.client, self.clock = database, settings, client, clock

    def dispatch_once(self):
        if not self.settings.telegram_enabled:
            return False
        now = self.clock()
        with self.db.write() as (s, _):
            row = s.scalar(select(Notification).where(Notification.due_ms <= now, or_(Notification.status == "PENDING", (Notification.status == "SENDING") & (Notification.lease_until_ms <= now))).order_by(Notification.id).limit(1))
            if not row:
                return False
            expected_bot = self.settings.telegram_bot_token.get_secret_value().split(":", 1)[0]
            if row.bot_id != expected_bot or row.chat_id != self.settings.telegram_chat_id.get_secret_value():
                row.status, row.error_code = "BLOCKED", "DESTINATION_CHANGED"
                return True
            if row.attempts >= self.settings.telegram_max_attempts:
                row.status, row.error_code = "FAILED", "MAX_ATTEMPTS"
                return True
            row.status, row.lease_until_ms = "SENDING", now + 30_000
            row.attempts += 1
            notification_id, chat_id, text, attempt = row.id, row.chat_id, row.body, row.attempts
        # No account lock or financial transaction is held during network I/O.
        try:
            message_id = self.client.send(chat_id, text)
        except DeliveryError as exc:
            with self.db.write() as (s, _):
                row = s.get(Notification, notification_id)
                if row.status != "SENDING" or row.attempts != attempt:
                    return True
                row.error_code, row.lease_until_ms = exc.code, 0
                if exc.permanent or row.attempts >= self.settings.telegram_max_attempts:
                    row.status = "FAILED"
                else:
                    row.status = "PENDING"
                    delay = exc.retry_after or min(300, 5 * 2 ** min(row.attempts - 1, 6))
                    row.due_ms = self.clock() + delay * 1000
            return True
        with self.db.write() as (s, _):
            row = s.get(Notification, notification_id)
            if row.status != "SENDING" or row.attempts != attempt:
                return True
            row.status, row.sent_ms, row.lease_until_ms = "SENT", self.clock(), 0
            row.telegram_message_id, row.error_code = message_id, None
        return True
