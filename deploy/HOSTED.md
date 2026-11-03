# Free cloud deployment

Source: private `HardikGenOne/sbsm-trading`, branch `main`.

- Render Free Python service: `sbsm-paper`, Singapore, `srv-dbl2s88ae00c73cear1g`, https://sbsm-paper.onrender.com.
- Vercel Hobby project: `sbsm-trading`, team `hardiks-projects-298dea7c`. Static assets are built by `scripts/build_dashboard.mjs`; `vercel.json` proxies authenticated dashboard paths to Render.
- Neon Free project: `sbsm-paper`, Singapore, `jolly-rice-08045623`. The existing SQLAlchemy engine and financial rules are preserved. Cloud credentials are separate from the laptop configuration.

The private local `.env.render` file contains cloud admin/webhook credentials and the TLS database connection. It is ignored by Git and Vercel. Render receives it as secret file `/etc/secrets/sbsm.env`; no database password is embedded in source or deployment arguments. Runtime state never depends on Render's temporary filesystem. Do not copy `.env.render` to the frontend.

Local verification: 70 tests, plus five opt-in accounting integration cases against Postgres. Integration cases use random disposable schemas and leave the application ledger untouched. Run them with `SBSM_TEST_POSTGRES=1 .venv/bin/python -m pytest tests/test_postgres.py` only when the intended private cloud configuration is present.

## Limitations and cutover

Render Free can sleep, restart, or exhaust shared workspace allowances. Neon Free also has storage and compute allowances. No always-on guarantee or keepalive cron is configured. A sleeping service can miss TradingView's short delivery deadline. This is a cloud paper-testing setup; check readiness before enabling actual alerts. [Render limits](https://render.com/docs/free), [Neon plans](https://neon.com/docs/introduction/plans).

Broker access remains deferred. No candle-price fills or live broker orders are enabled. Telegram requires verified private destination configuration before messages are sent. The Render proxy chain has not yet been verified against a genuine TradingView delivery: the source-IP gate remains fail closed instead of trusting arbitrary forwarded headers.

The cloud begins with a separate ₹500,000 paper ledger. The laptop ledger is not silently merged. Never direct the same intended portfolio's alerts to both ledgers. Do not configure TradingView until origin verification, quote readiness, and a deliberate cutover are complete.

Render auto-deployment is off. Commit and push meaningful changes, then deploy a tested commit deliberately. Monitor `/health/ready` for operational health and the authenticated dashboard for broker/quote readiness. Access and authentication should be verified through the final Vercel address before relying on remote controls.
