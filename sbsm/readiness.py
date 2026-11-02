"""Passive operational checks; never changes positions, cash, or broker state."""
from sqlalchemy import func, select

from .models import Event, Quote


def report(engine):
    now = engine.clock()
    settings = engine.settings
    portfolio = engine.portfolio()
    checks = []

    def add(name, status, message):
        checks.append({"name": name, "status": status, "message": message})

    age = now - portfolio["worker_heartbeat_ms"]
    worker_ok = 0 <= age < max(30000, int(settings.poll_seconds * 10000))
    add("worker", "PASS" if worker_ok else "BLOCKED", "Worker heartbeat is recent." if worker_ok else "Start or restart the paper worker.")
    configured = bool(settings.kite_api_key.get_secret_value() and settings.kite_access_token.get_secret_value())
    if settings.feed_mode != "kite":
        add("feed", "BLOCKED", "Manual quotes are synthetic; select Kite mode for live-data paper trading.")
    elif not configured:
        add("feed", "BLOCKED", "Configure market-data credentials locally with scripts/kite_login.py, then restart the API and worker.")
    else:
        add("feed", "PASS", "Market-data credentials are configured; freshness below verifies usable quote delivery.")

    market_open = False
    calendar_valid = True
    try:
        market_open = engine.calendar.is_open(now)
        add("market_session", "PASS" if market_open else "WAITING", "NSE session is open." if market_open else "Market is closed; fresh fill readiness can be verified during the next session.")
    except RuntimeError:
        calendar_valid = False
        add("market_session", "BLOCKED", "Market calendar needs review; execution remains blocked.")

    with engine.db.read() as session:
        last_quote = session.scalar(select(Quote).where(Quote.source == settings.feed_mode).order_by(Quote.asof_ms.desc()))
        pending = session.scalar(select(func.count()).select_from(Event).where(Event.status == "QUEUED"))
        oldest = session.scalar(select(func.min(Event.received_ms)).where(Event.status == "QUEUED"))
    fresh = bool(last_quote and 0 <= now - last_quote.asof_ms <= settings.quote_max_age_seconds * 1000)
    quote_status = "PASS" if fresh else ("BLOCKED" if market_open else "WAITING")
    add("quotes", quote_status, "A recent market snapshot is available." if fresh else "No fresh snapshot yet. No candle-price fallback or fabricated fills will be used.")
    add("entry_controls", "BLOCKED" if portfolio["entries_paused"] or portfolio["reconciliation_required"] else "PASS", "New entries are paused or require reconciliation." if portfolio["entries_paused"] or portfolio["reconciliation_required"] else "New entries are enabled.")
    queue_age = max(0, now - oldest) if oldest is not None else 0
    add("queue", "BLOCKED" if queue_age > 30000 else "PASS", "Signal queue is delayed; inspect worker health." if queue_age > 30000 else "No delayed signal backlog.")
    return {
        "purpose": "live-data paper trading",
        "mode": "paper",
        "ready_for_new_entries": all(check["status"] == "PASS" for check in checks),
        "checks": checks,
        "pending_signals": pending,
        "oldest_queued_age_ms": queue_age,
        "latest_quote_ms": last_quote.asof_ms if last_quote else None,
        "market_open": market_open,
        "calendar_valid": calendar_valid,
        "tradingview_delivery_verified": None,
        "tradingview_note": "End-to-end delivery must be checked independently against the TradingView alert log; this report cannot prove origin or delivery.",
    }
