# Free cloud deployment

Source: private `HardikGenOne/sbsm-trading`, branch `main`.

- Render Free Python service: `sbsm-paper`, Singapore, `srv-dbl2s88ae00c73cear1g`, https://sbsm-paper.onrender.com.
- Vercel Hobby project: `sbsm-trading`, team `hardiks-projects-298dea7c`. Static assets are built by `scripts/build_dashboard.mjs`; `vercel.json` proxies authenticated dashboard paths to Render.
- Neon Free project: `sbsm-paper`, Singapore, `jolly-rice-08045623`. The existing SQLAlchemy engine and financial rules are preserved. Cloud credentials are separate from the laptop configuration.

The private local `.env.render` file contains cloud admin/webhook credentials and the TLS database connection. It is ignored by Git and Vercel. Render receives it as secret file `/etc/secrets/sbsm.env`; no database password is embedded in source or deployment arguments. Runtime state never depends on Render's temporary filesystem. Do not copy `.env.render` to the frontend.

Local verification: 70 tests, plus five opt-in accounting integration cases against Postgres. Integration cases use random disposable schemas and leave the application ledger untouched. Run them with `SBSM_TEST_POSTGRES=1 .venv/bin/python -m pytest tests/test_postgres.py` only when the intended private cloud configuration is present.

## Limitations and cutover

Render Free can sleep, restart, or exhaust shared workspace allowances. Neon Free also has storage and compute allowances. cron-job.org runs GET /health/ready every minute on weekdays in the 11:05–11:16, 13:05–13:16, 15:05–15:16 and 15:20–15:29 IST windows. Response history is enabled. There is no always-on guarantee. A sleeping service can miss TradingView's short delivery deadline. This is a cloud paper-testing setup; check readiness before enabling actual alerts. [Render limits](https://render.com/docs/free), [Neon plans](https://neon.com/docs/introduction/plans).

Broker access remains deferred. No candle-price fills or live broker orders are enabled. Telegram is enabled for the verified private chat; a connection test was delivered by the hosted outbox on 4 November 2026 (SENT, first attempt). Render-specific source verification uses its documented edge-overwritten CF-Connecting-IP header. A live forged-header probe was blocked at the edge; normal requests resolve through the configured mode. The first genuine TradingView delivery remains unverified. Generic forwarded IP headers remain untrusted.

The cloud begins with a separate ₹500,000 paper ledger. The laptop ledger is not silently merged. Never direct the same intended portfolio's alerts to both ledgers. Use GETTING_STARTED.md to configure a first TradingView alert for receipt verification. Simulated fills still require broker quote readiness; do not treat successful receipt as execution readiness. A deliberate cutover remains necessary.

Render auto-deployment is off. Commit and push meaningful changes, then deploy a tested commit deliberately. Monitor `/health/ready` for operational health and the authenticated dashboard for broker/quote readiness. Access and authentication should be verified through the final Vercel address before relying on remote controls.

User walkthrough: [GETTING_STARTED.md](../GETTING_STARTED.md). The complete private webhook URL is generated locally in ignored `work/tradingview-webhook-url.txt`; its contents must never be committed.


The HEAD warm-up test verified an already warm server only. Subsequent scheduled
HEAD checks returned 502 while the service was unavailable. Repeated GET requests
replace the two isolated attempts per candle. A warm GET test verifies current
reachability and database/worker readiness. The next cold start and TradingView
delivery still require verification against the three-second webhook deadline.
Check scheduled history before the next signal window. Free hosting remains a
best-effort paper environment.
