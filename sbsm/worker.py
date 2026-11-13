"""Run separately from the API: python -m sbsm.worker."""
import logging
import signal
import threading
from sqlalchemy import select

from .config import Settings, SYMBOLS
from .database import Database
from .engine import PaperEngine, now_ms
from .market import MarketCalendar, quote_feed
from .models import Audit
from .notifications import enqueue
from .receiver import drain_inbox
from .startup_quotes import warm_dhan, recover_startup

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
    warmup_pending = False
    with db.read() as session:
        transition = session.scalar(select(Audit).where(Audit.action.in_(("WORKER_ERROR", "WORKER_RECOVERED"))).order_by(Audit.id.desc()))
        error_active = bool(transition and transition.action == "WORKER_ERROR")
    log.info("Paper worker started; feed=%s; no live order execution exists", settings.feed_mode)
    try:
        while not stop.is_set():
            try:
                with db.read() as s:
                    selection = engine.active_feed(s)
                if provider != selection[0]:
                    if feed is not None:
                        feed.close()
                    provider, feed = selection[0], quote_feed(settings, selection[0])
                    if provider == 'dhan':
                        warmup_pending = not warm_dhan(engine, feed, selection)
                # Every Dhan worker startup/switch attempts the bounded read-only
                # warm-up before admitting receipts. Failed tests never drop them.
                drain_inbox(engine)
                engine.step()
                if feed is not None and calendar.is_open(now_ms()):
                    # A single read-only probe keeps feed readiness observable before
                    # the first signal; never synthesize an entry just to test access.
                    symbols = list(SYMBOLS) if provider == "dhan" else (engine.watch_symbols() or ["ICICIBANK"])
                    quotes = feed.fetch(symbols)
                    for quote in quotes:
                        engine.quote(quote, provider, selection=selection, observed_ms=getattr(feed, "observed_ms", None))
                    if provider == 'dhan' and warmup_pending:
                        warmup_pending = not recover_startup(engine, quotes)
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
