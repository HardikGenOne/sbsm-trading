"""Run separately from the API: python -m sbsm.worker."""
import logging
import signal
import threading

from .config import Settings
from .database import Database
from .engine import PaperEngine, now_ms
from .market import KiteQuotes, MarketCalendar
from .models import Audit

log = logging.getLogger("sbsm.worker")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    db = Database(settings.database_url)
    db.initialize(settings.initial_capital_paise, now_ms())
    calendar = MarketCalendar(settings.closed_dates)
    engine = PaperEngine(db, settings, calendar)
    feed = KiteQuotes(settings)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    last_error_at = 0
    log.info("Paper worker started; feed=%s; no live order execution exists", settings.feed_mode)
    try:
        while not stop.is_set():
            try:
                engine.step()
                if settings.feed_mode == "kite" and calendar.is_open(now_ms()):
                    # A single read-only probe keeps feed readiness observable before
                    # the first signal; never synthesize an entry just to test access.
                    symbols = engine.watch_symbols() or ["ICICIBANK"]
                    for quote in feed.fetch(symbols):
                        engine.quote(quote, "kite")
                engine.step()
            except Exception as exc:
                # Exception bodies may contain broker URLs/credentials. Log class only.
                if now_ms() - last_error_at > 60000:
                    last_error_at = now_ms()
                    log.error("Worker iteration failed (%s); durable state retained", type(exc).__name__)
                    try:
                        with db.write() as (s, _):
                            s.add(Audit(time_ms=now_ms(), action="WORKER_ERROR", detail=type(exc).__name__))
                    except Exception:
                        log.error("Could not persist worker health event")
            stop.wait(settings.poll_seconds)
    finally:
        feed.close()
        db.engine.dispose()


if __name__ == "__main__":
    main()
