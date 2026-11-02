"""Independent notification delivery and worker-heartbeat monitoring."""
import logging
import signal
import threading

from .config import Settings
from .database import Database
from .engine import now_ms
from .models import NotificationState
from .notifications import Dispatcher, TelegramClient, enqueue

log = logging.getLogger("sbsm.notifier")


def watch_worker(db, settings, clock=now_ms):
    now = clock()
    with db.write() as (s, account):
        state = s.get(NotificationState, "worker_watch")
        if state is None:
            state = NotificationState(key="worker_watch", value={"first_seen": now, "down": False})
            s.add(state)
        previous = state.value
        heartbeat = account.worker_heartbeat_ms
        down = now - heartbeat > 90_000 if heartbeat else now - previous["first_seen"] > 90_000
        if down != previous["down"]:
            enqueue(s, settings, f"worker-watch:{'down' if down else 'up'}:{now}", "WORKER HEARTBEAT MISSING" if down else "WORKER HEARTBEAT RECOVERED", ["No new paper entries can execute while the worker is stopped." if down else "The worker heartbeat is recent again. Check feed readiness separately."], now)
        state.value = {**previous, "down": down}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    if not settings.telegram_enabled:
        log.info("Telegram is disabled; notification process not started")
        return
    db = Database(settings.database_url)
    db.initialize(settings.initial_capital_paise, now_ms())
    client = TelegramClient(settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher(db, settings, client, now_ms)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    last_error_at = 0
    try:
        while not stop.is_set():
            try:
                watch_worker(db, settings)
                dispatcher.dispatch_once()
            except Exception as exc:
                if now_ms() - last_error_at > 60_000:
                    last_error_at = now_ms()
                    log.error("Notification iteration failed (%s); queued messages retained", type(exc).__name__)
            stop.wait(settings.notification_poll_seconds)
    finally:
        client.close()
        db.engine.dispose()


if __name__ == "__main__":
    main()
