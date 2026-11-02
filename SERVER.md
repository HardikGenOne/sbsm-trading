# SBSM paper-trading server

This is a local, persistent paper engine with a browser dashboard. No broker order-placement endpoint or live execution adapter exists. Authenticated JSON endpoints and Swagger UI are also available.

Cloud hosting and outbound Telegram notifications now have a prepared deployment path. See [CLOUD.md](CLOUD.md) for the supervised container stack, private bot setup, persistence/backups and remaining hosting-access requirements. Preparing these files does not mean a remote service or real Telegram recipient is connected.

## Run

From this project folder:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/bootstrap.py
.venv/bin/python scripts/run_local.py
```

The first two steps are already completed in this checkout. Bootstrap preserves an existing `.env`; new credentials are random and written with mode 0600 without being printed.

- Dashboard: http://127.0.0.1:8000/
- API and Swagger: http://127.0.0.1:8000/docs
- Liveness: http://127.0.0.1:8000/health
- Run tests: `.venv/bin/python -m pytest`
- Isolated walkthrough: `.venv/bin/python scripts/demo.py`
- Passive readiness check: `.venv/bin/python scripts/check_readiness.py`
- Read-only broker probe: `.venv/bin/python scripts/check_readiness.py --probe-market-data`
- Stop both processes with Ctrl+C.

In Swagger, use **Authorize**, entering `SBSM_ADMIN_TOKEN` from your local `.env` as the Bearer credential. Do not share `.env` or paste credentials into chat. The two secrets have separate purposes: the admin token cannot authenticate a webhook, and the webhook token cannot access portfolio/control endpoints.

## Dashboard without broker access

The dashboard initially opens in **Demo sandbox** with explicitly illustrative values. Demo navigation, stock filters, signal inspection, pause/resume, pending-entry cancellation and exit requests work in browser memory only. Reloading or reselecting the sandbox resets it. Demo actions never call account-control endpoints or modify the persistent paper ledger. Example activity, fills and ledger excerpts are not measured strategy performance or a complete accounting dataset.

Choose **My paper account** or **Connect paper account** and enter the admin token from `.env` to inspect actual server records, even without broker access. The initial empty account shows ₹5,00,000 and no fabricated positions or returns. The dashboard provides overview, positions, orders, execution fills, signals, skipped decisions, closed trades, ledger and readiness views. It polls actual-account reports every five seconds while visible and while no dialog is open. Collection views load the latest 100 records, expandable to 500; use the paginated APIs for older history. Signal inspection retrieves the entire cycle's signals, orders, fills, ledger and position independently of the current table limit.

The admin token is used only to create a signed browser session and is not stored in localStorage or sessionStorage. The session cookie is HttpOnly, SameSite=Strict and expires after one hour by default. Cookie-authenticated control requests require both a same-origin header and a session-bound CSRF token. Existing Bearer API clients and TradingView webhooks continue to work without browser CSRF tokens. Logout clears the browser cookie; signed sessions otherwise expire naturally. Rotating the admin token invalidates existing sessions once the API restarts. Static assets have no external CDN or font dependency, and the dashboard sets a restrictive Content Security Policy.

Local HTTP uses `SBSM_SESSION_COOKIE_SECURE=false`. Set it to true for HTTPS deployment and configure the trusted reverse proxy correctly before remote access. The demo page itself is public and contains no account data; actual account reports and controls remain authenticated. This local single-owner login is not a multi-user identity system or a completed internet deployment.

For separate terminals, run `uvicorn sbsm.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log` and `python -m sbsm.worker` using the project's virtual environment. Use one worker for this SQLite deployment. Database locking also protects against accidental overlapping workers, but extra workers add contention and unnecessary feed polling.

## Real market-data setup

Default `SBSM_FEED_MODE=kite` uses **read-only** Kite HTTP quote snapshots and the NSE instrument master. It requires a Kite app with live-market-data permissions and an active session. A TradingView subscription does not provide this feed.

1. Configure the Kite app's redirect URL in its developer console.
2. Run `.venv/bin/python scripts/kite_login.py` locally.
3. Complete Zerodha's official login manually and provide the temporary request token and API secret to the local hidden prompts. The secret is not stored. The access token and API key are saved privately in `.env`.
4. Restart both the API and worker after every session update, so execution and API configuration reports agree. Sessions expire; this code does not automate credentials/TOTP.
5. Observe `/api/status`, `/api/audit`, and quote timestamps in `/api/portfolio` during market hours.

The feed fetches actual tick sizes daily, then best bid/ask prices and their displayed quantities. It ignores instruments without complete two-sided depth. Missing/invalid credentials or unavailable quotes do not produce fake fills. Entries reserve capital, wait, and expire. Exits stay pending until tradable quotes return. Feed failures produce credential-free audit records.

Before any positions or pending orders exist, the worker polls one approved stock (`ICICIBANK`) during market hours to establish feed readiness. It does not create signals or trades for this probe. `/api/readiness` distinguishes a stopped worker, missing credentials, manual-only data, stale quotes, closed sessions, calendar failures, paused entries, and queue delays. `ready_for_new_entries` is an operational check for live-data paper trading, not a promise that an individual signal passes sizing or symbol checks. TradingView delivery is reported as unknown; verify it separately against its alert log.

The readiness script sends the admin credential only to the fixed local API address and does not follow redirects. Its optional broker probe performs read-only instrument/quote requests and reports sanitized error categories. It never submits a signal, places an order, or changes the paper ledger. Exit code 1 means checks are blocked or waiting; a closed market is an expected waiting state.

`SBSM_FEED_MODE=manual` enables authenticated `POST /paper/quotes` for controlled testing. It is explicitly synthetic data; do not use it for live-data paper performance measurement. Manual quotes are forbidden in Kite mode. Neither mode uses a TradingView reference price as an execution fill.

## TradingView integration

Keep the Pine file and its contract from `README.md`. Configure the alert for **alert() function calls only**. The webhook URL is:

```text
https://YOUR-HTTPS-HOST/webhooks/tradingview/YOUR-WEBHOOK-TOKEN
```

Read `SBSM_WEBHOOK_TOKEN` locally from `.env`. The server returns 202 only after committing the event. A duplicate returns the original sequence and `duplicate=true`. A conflicting payload with the same event ID is quarantined and returns 409. A temporary database error returns 503 instead of acknowledging an unsaved event. Schema errors return 422. Ingress accepts at most 16 KB per request and 120 new signals/minute.

The secret URL is a bearer credential, not proof of origin. Before an internet deployment, put the API behind HTTPS, redact webhook paths in reverse-proxy logs, enforce edge rate/request/connection limits, and configure TradingView-origin verification. Keep portfolio/control routes behind authentication. Access logs are disabled in the supplied local launcher to avoid logging the webhook token. This localhost server is not yet reachable by TradingView; cloud deployment is separate work.

Old static examples are contract fixtures, not current trading instructions. Stale entries are durably skipped. Do not blindly replay them against your paper account. Use `scripts/demo.py` for a current, isolated walkthrough.

## Portfolio rules

- Initial pool: ₹5,00,000. Once initialized, the database owns it; changing the environment does not reset an existing account.
- Pool = initial capital + net realized P&L on sold shares. Open-position valuation never increases allocations. Entry fees are included in position cost basis and recognized proportionally as shares are sold.
- Each new entry gets 12% of the pool at its processing time, including a cost/slippage allowance. A ₹10 lakh pool means a ₹1.2 lakh budget.
- Available cash = cash minus pending-entry reserves minus unsettled sale proceeds. Cash already includes reserved cash; do not subtract reserves again when calculating equity.
- Entries are admitted by committed signal sequence. The budget is reserved in the same transaction as acceptance. Exits receive execution priority.
- If available cash is below the full budget, the signal is skipped permanently. No partial allocation, delayed replay, leverage, or short selling.
- Only one active position or pending entry per stock. Quantities are whole shares. An unaffordable share produces a skip and releases the reservation.
- There are no stop-loss, profit-target, daily-loss, or end-of-day liquidation rules. Positions remain overnight until a matching signal or authenticated manual close request.
- Starting an alert during an already-open Pine cycle creates no catch-up entry. A matching exit of a skipped/unknown cycle cannot open a short or close a different cycle. An unmatched exit while another cycle in that stock is open blocks new entries pending operator review.
- Pause affects **new admissions**; accepted pending entries stay active. Cancel them explicitly when wanted. Exits remain operational while paused.

## Execution and settlement assumptions

Orders are simulated protected-market entries and market exits. Entries wait at least the configured latency and require a quote observed after that deadline. Fills use ask for buys and bid for sells, apply adverse slippage, and round to the instrument tick. Entry prices above the configured protection band wait until their TTL expires. Exits have no price-based stop or expiry and are never rejected merely for stale signal age.

Only a configurable fraction of top-of-book quantity is available per snapshot (10% by default). Used depth is persisted across orders and worker restarts. The same order cannot fill twice against the same snapshot. Partial entry cancellation preserves its filled position. Exit intent cancels an unfinished entry before closing its actual filled quantity. Queued entry+exit pairs received during downtime cannot open a late position.

This is a top-of-book polling approximation, not an exchange matching engine: queue position, hidden liquidity, transient price paths, circuit dynamics, T1 holding sale restrictions, and exact broker costs/settlement are not fully reproduced. Charge defaults are **conservative configurable estimates**, not verified Zerodha contract-note tariffs: 15 bps per side plus ₹20 once per sell order, including partial fills. They are not itemized statutory charges. Tune against actual delivery charges before assessing performance. The simulator may underfill liquidity; it does not claim exact live execution.

Default settlement is deliberately conservative: all net sale proceeds are held until next trading-session open. Pool P&L updates on the sell fill, but held cash cannot fund a new entry. `immediate` settlement is available only as an explicit simulation assumption. This is not a claim that Zerodha offers identical fund availability.

The NSE schedule comes from `pandas-market-calendars`, including its curated holidays. The installed calendar documents coverage through 2026; the implementation fails closed outside that reviewed range. Review/update the calendar before 2027 and for exceptional exchange closures. `SBSM_CLOSED_DATES` can add exclusions without changing code. Special sessions rely on the calendar's published schedule. These calendar/cost/settlement assumptions are part of every performance interpretation.

## JSON endpoints

All `/api/*`, `/controls/*`, and `/paper/*` endpoints require `Authorization: Bearer ADMIN_TOKEN` or a valid dashboard browser session. Cookie-authenticated mutations additionally require CSRF protection.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/portfolio` | Pool, cash, reserves, unsettled proceeds, P&L and open positions |
| `GET /api/summary` | Total signals, skipped decisions, active orders and closed trades |
| `GET /api/status` | Worker heartbeat, market session, configuration and recent audit |
| `GET /api/readiness` | Passive readiness checklist for live-data paper execution |
| `GET /api/notifications` | Telegram queue counts and sanitized delivery status |
| `POST /controls/notifications/test` | Queue a non-financial test to the configured chat |
| `POST /controls/notifications/{id}/retry` | Explicit retry of a failed message for its original destination |
| `GET /api/signals` | Durable receipt sequence, decision and outcome |
| `GET /api/signals/{sequence}/trace` | Full trade-cycle signals, orders, fills, ledger and position |
| `GET /api/skipped` | Separate skipped-signal list with allocation details/reasons |
| `GET /api/orders` | Pending, partial, filled, cancelled and expired orders |
| `GET /api/fills` | Individual fills with quantity, price and modeled fees |
| `GET /api/trades` | Closed paper trade cycles and net realized results |
| `GET /api/ledger` | Append-only balance changes with balance snapshots |
| `GET /api/conflicts` | Quarantined duplicate-ID payload conflicts |
| `GET /api/audit` | Operator actions and sanitized worker errors |
| `POST /controls/entries` | `{"paused": true}` or `false` |
| `POST /controls/orders/{id}/cancel` | Cancel remaining quantity of a pending paper entry |
| `POST /controls/positions/close` | `{"cycle_id":"..."}`; durable operator-requested exit |
| `POST /controls/reconciliation/acknowledge` | `{"note":"...at least 20 characters..."}` after reviewing mismatch |
| `POST /paper/quotes` | Inject a manual quote only in manual feed mode |

Collection endpoints use `limit` (max 500), `after` sequence pagination and optional `order=desc` (default remains ascending); signals/orders also accept `status`. Closed trades use `offset` and optional `order=desc`. **All monetary values in accounting APIs are integer paise**; divide by 100 to display rupees. Signal reference prices remain rupees, as in Pine. Fresh complete equity is `null` when any position lacks a fresh mark, rather than silently treating missing holdings as zero. Old marks may appear on individual positions with `quote_stale=true`.

## Code structure

```text
sbsm/
  api.py          FastAPI ingress, authentication, reports and controls
  config.py       Validated settings and approved universe
  database.py     Transactions, SQLite WAL, serialized account writes
  engine.py       Allocation, reservations, order/fill/P&L state machine
  market.py       NSE sessions and read-only Kite quote adapter
  readiness.py    Passive live-data paper-readiness checklist
  models.py       Persistent accounting and execution entities
  schemas.py      Pine payload and quote validation
  sessions.py     Signed browser sessions and CSRF tokens
  notifications.py  Transactional Telegram outbox and retry delivery
  notifier.py     Independent dispatcher and heartbeat transitions
  migrations.py   Additive notification schema migration
  service.py      API/worker/notifier supervision
  backup.py       Consistent private SQLite snapshots
  static/         Responsive dashboard HTML, CSS and JavaScript
  worker.py       Persistent-queue consumer and quote polling process
scripts/
  bootstrap.py    Private local configuration
  run_local.py    Local API + worker supervisor
  kite_login.py   Official manual-login token exchange helper
  demo.py         Disposable deterministic walkthrough
  check_readiness.py  Safe local diagnostics and optional read-only broker probe
tests/            Financial invariants, concurrency, restart and API tests
data/             Private database files (gitignored)
```

SQLite is intentional for this first local single-account release: no Redis or database service is required to start. The worker queue lives in SQL, never in memory. Writes serialize via `BEGIN IMMEDIATE`; API network calls and quote polling do not hold account locks. SQLAlchemy includes a PostgreSQL row-lock path, but PostgreSQL deployment/migrations have not been integration-tested here. Do not treat it as a verified production migration.

Back up the database with SQLite's online backup API (not a bare copy of the `.db` while WAL writes are active), and separately retain `.env` securely. Do not reset/delete a running paper ledger. The initial schema is created on startup; future schema changes need explicit migrations. Remote deployment, live broker orders and automatic trading-performance claims are not included in this milestone.

## Primary references

- [TradingView webhook behavior](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)
- [Kite quotes and instrument metadata](https://kite.trade/docs/connect/v3/market-quotes/)
- [Kite manual login/session flow](https://kite.trade/docs/connect/v3/user/)
- [NSE calendar library and update caveats](https://pandas-market-calendars.readthedocs.io/en/latest/)
- [SQLite immediate transactions](https://www.sqlite.org/lang_transaction.html)
