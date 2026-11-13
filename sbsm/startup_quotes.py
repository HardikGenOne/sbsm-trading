"""Read-only Dhan warm-up before queued signals are admitted on worker start."""
import httpx

from .config import SYMBOLS
from .engine import now_ms
from .models import Audit, NotificationState
from .notifications import enqueue

KEY = 'dhan_startup'


def save_probe(engine, value, notify=False):
    with engine.db.write() as (session, _):
        current = session.get(NotificationState, KEY)
        if current is None:
            session.add(NotificationState(key=KEY, value=value))
        else:
            current.value = value
        if notify:
            audit = Audit(time_ms=value['completed_ms'], action='DHAN_WARMUP', detail=value['status'])
            session.add(audit)
            session.flush()
            lines = [value['message'], f"Usable quotes: {value['quote_count']}; fresh quotes: {value['fresh_count']}"]
            sample = value.get('sample')
            if sample:
                lines.append(f"{sample['symbol']} LTP: ₹{sample['last_price']}")
            enqueue(session, engine.settings, f'audit:{audit.id}', 'DHAN STARTUP ' + value['status'], lines, value['completed_ms'])


def probe_result(engine, quotes, started_ms, clock=now_ms):
    now = clock()
    market_open = engine.calendar.is_open(now)
    fresh = [q for q in quotes if 0 <= now-q.asof_ms <= engine.settings.quote_max_age_seconds*1000]
    ready = market_open and bool(fresh)
    sample = next((q for q in quotes if q.symbol == 'ICICIBANK'), next(iter(quotes), None))
    return {
        'started_ms': started_ms, 'completed_ms': now,
        'status': 'READY' if ready else 'WAITING',
        'quote_count': len(quotes), 'fresh_count': len(fresh), 'market_open': market_open,
        'message': 'Dhan returned fresh quotes; paper-fill checks remain active.' if ready else
                   'Dhan request completed; market is closed. Fresh quotes will be checked at the next session.' if not market_open else
                   'Dhan request completed, but no fresh two-sided quote is available yet. Automatic polling continues.',
        'sample': {'symbol': sample.symbol, 'last_price': str(sample.last_price), 'asof_ms': sample.asof_ms} if sample else None,
    }


def recover_startup(engine, quotes, clock=now_ms):
    with engine.db.read() as session:
        previous = session.get(NotificationState, KEY)
        value = dict(previous.value) if previous else None
    if not value:
        return False
    if value['status'] == 'READY':
        return True
    result = probe_result(engine, quotes, value['started_ms'], clock)
    if result['status'] == 'READY':
        save_probe(engine, result, notify=True)
        return True
    return False


def warm_dhan(engine, feed, selection, clock=now_ms):
    started = clock()
    save_probe(engine, {'status': 'WARMING', 'started_ms': started, 'completed_ms': None,
                        'quote_count': 0, 'fresh_count': 0,
                        'message': 'Loading Dhan instruments and requesting startup quotes before signal admission.'})
    try:
        quotes = feed.fetch(list(SYMBOLS))
        for quote in quotes:
            engine.quote(quote, 'dhan', selection=selection, observed_ms=getattr(feed, 'observed_ms', None))
        result = probe_result(engine, quotes, started, clock)
    except Exception as exc:
        # Never expose broker URLs, credentials or exception bodies in status.
        code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        message = 'Dhan startup test failed. Automatic quote polling will retry; inspect market-data health.'
        if code in (401, 403):
            message = 'Dhan rejected the token or data permissions. Update the token in Render and redeploy.'
        elif code == 429:
            message = 'Dhan rate-limited the startup test. Shared cooldown and automatic polling will retry.'
        result = {'status': 'FAILED', 'started_ms': started, 'completed_ms': clock(),
                  'quote_count': 0, 'fresh_count': 0, 'error_code': code,
                  'message': message, 'sample': None}
    save_probe(engine, result, notify=True)
    return result['status'] == 'READY'
