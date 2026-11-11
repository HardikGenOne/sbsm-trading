"""Run separately from the API: python -m sbsm.worker."""
import logging
import signal
import threading
from sqlalchemy import select

from .config import Settings
from .database import Database
from .engine import PaperEngine, now_ms
from .market import MarketCalendar, quote_feed
from .models import Audit
from .notifications import enqueue
from .receiver import drain_inbox

log = logging.getLogger("sbsm.worker")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    db = Database(settings.database_url)
    db.initialize(settings.initial_capital_paise, now_ms())
    calendar = MarketCalendar(settings.closed_dates)
    engine = PaperEngine(db, settings, calendar)
    provider, feed = None, None
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    last_error_at = 0
    with db.read() as session:
        transition = session.scalar(select(Audit).where(Audit.action.in_(("WORKER_ERROR", "WORKER_RECOVERED"))).order_by(Audit.id.desc()))
        error_active = bool(transition and transition.action == "WORKER_ERROR")
    log.info("Paper worker started; feed=%s; no live order execution exists", settings.feed_mode)
    try:
        while not stop.is_set():
            try:
                drain_inbox(engine)
                engine.step()
                with db.read() as s:
                    selection = engine.active_feed(s)
                if provider != selection[0]:
                    if feed is not None:
                        feed.close()
                    provider, feed = selection[0], quote_feed(settings, selection[0])
                if feed is not None and calendar.is_open(now_ms()):
                    # A single read-only probe keeps feed readiness observable before
                    # the first signal; never synthesize an entry just to test access.
                    symbols = engine.watch_symbols() or ["ICICIBANK"]
                    for quote in feed.fetch(symbols):
                        engine.quote(quote, provider, selection=selection)
                engine.step()
                if error_active:
                    with db.write() as (s, _):
                        audit = Audit(time_ms=now_ms(), action="WORKER_RECOVERED", detail="Processing iteration completed")
                        s.add(audit)
                        s.flush()
                        enqueue(s, settings, f"audit:{audit.id}", "PROCESSING RECOVERED", ["Paper processing resumed. Review readiness before relying on quote delivery."], now_ms())
                    error_active = False
            except Exception as exc:
                # Exception bodies may contain broker URLs/credentials. Log class only.
                if now_ms() - last_error_at > 60000:
                    last_error_at = now_ms()
                    log.error("Worker iteration failed (%s); durable state retained", type(exc).__name__)
                if not error_active:
                    try:
                        with db.write() as (s, _):
                            audit = Audit(time_ms=now_ms(), action="WORKER_ERROR", detail=type(exc).__name__)
                            s.add(audit)
                            s.flush()
                            enqueue(s, settings, f"audit:{audit.id}", "PROCESSING ERROR", [f"Error category: {type(exc).__name__}", "Durable trading state is retained. Inspect system health."], now_ms())
                        error_active = True
                    except Exception:
                        log.error("Could not persist worker health event")
            stop.wait(settings.poll_seconds)
    finally:
        if feed is not None:
            feed.close()
        db.engine.dispose()


if __name__ == "__main__":
    main()
