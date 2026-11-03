# Next milestones

## 1. Validate the market-data paper pipeline

- Configure Zerodha's market-data app/session locally using scripts/kite_login.py; do not share secrets in chat or Git.
- Restart API and worker and confirm fresh real bid/ask quotes, actual tick sizes, and worker health during a market session.
- Exercise one real-time TradingView entry and matching exit through a temporary HTTPS ingress or the planned server deployment. Check the alert log against the durable signal, allocation, order, fills, and ledger.
- Verify a skipped entry's later exit creates no order. Verify duplicate delivery and service restart with a pending order.
- Keep the starting paper account separate from test/demo ledgers. No live broker order adapter is included.

## 2. Improve execution fidelity before assessing results

- Replace estimated aggregate charges with itemized delivery-trade charges checked against actual broker statements; account for same-day exits separately.
- Review broker-specific settlement/available-funds rules and T1-holding sale eligibility. Current proceeds availability is deliberately conservative.
- Add configurable price-band/circuit and liquidity handling, and consider WebSocket quotes if polling limits fidelity.
- Review NSE holidays/special sessions and update the calendar before 2027. Add fail-closed monitoring around calendar coverage.
- Record explicit model/configuration versions with a paper run so results from different assumptions are distinguishable.

## 3. Build the private dashboard

Local dashboard delivered: isolated browser demo, authenticated actual-account views, cookie/CSRF sessions, mobile layout, paper controls, recent-first records and complete cycle traces. Broker credentials remain deferred. The user resumed GitHub login and source publishing on 4 November 2026; free Render/Vercel hosting with Neon SQL is live. See deploy/HOSTED.md for verification and execution-readiness limits.

- Portfolio: fund pool, available cash, reservations, unsettled proceeds, realized/unrealized P&L and quote freshness.
- Positions, orders, fills, closed trades, and a separate skipped-signal list with reasons and sizing details.
- Signal-to-ledger event trace and system health: feed, authentication, queue lag, heartbeat and reconciliation alerts.
- Authenticated controls for pausing entries, cancelling pending entries, requesting exits and acknowledging reviewed mismatches.

## 4. Deploy for continuous access

Deployment source, supervised processes, HTTPS Compose configuration, production host/source-IP checks, online backup tool and durable Telegram notifications are now prepared. Render and Vercel public deployments, durable Neon SQL, private-account login and origin/CSRF controls are verified. Broker connection, Telegram binding, genuine TradingView ingress and continuous-runtime guarantees remain outstanding.

- Private authenticated dashboard, HTTPS TradingView ingress with origin verification, secret-path redaction and edge limits.
- Persistent supervised worker, database backups, recovery drill, and useful error alerts without routine noise.
- Choose the production database and implement/test migrations before moving the local ledger. Verify market-data access from the chosen host.
- Static outbound IP is required for a future live broker order integration; no live activation is part of this milestone.

## 5. Paper observation and later live readiness

- Judge operational readiness by completed trade cycles and deliberate failure tests, not elapsed days alone.
- Reconcile realized P&L, fees, reservations, settlement and positions with the fill ledger.
- Only after the user explicitly requests live execution: implement a separate broker execution adapter and test reconciliation/uncertain-order recovery before any activation.

## Git workflow

Every meaningful change is tested as appropriate, committed with a descriptive message and pushed to the configured GitHub repository. See AGENTS.md. Authentication/push failures must be reported, never described as successful publication.
