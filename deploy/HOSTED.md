# Free cloud deployment

Source: private `HardikGenOne/sbsm-trading`, branch `main`.

- Render Free Python service: `sbsm-paper`, Singapore, `srv-dbl2s88ae00c73cear1g`, https://sbsm-paper.onrender.com.
- Vercel Hobby project: `sbsm-trading`, team `hardiks-projects-298dea7c`. Static assets are built by `scripts/build_dashboard.mjs`; `vercel.json` proxies authenticated dashboard paths to Render.
- Neon Free project: `sbsm-paper`, Singapore, `jolly-rice-08045623`. The existing SQLAlchemy engine and financial rules are preserved. Cloud credentials are separate from the laptop configuration.

The private local `.env.render` file contains cloud admin/webhook credentials and the TLS database connection. It is ignored by Git and Vercel. Render receives it as secret file `/etc/secrets/sbsm.env`; no database password is embedded in source or deployment arguments. Runtime state never depends on Render's temporary filesystem. Do not copy `.env.render` to the frontend.

Local verification: 70 tests, plus five opt-in accounting integration cases against Postgres. Integration cases use random disposable schemas and leave the application ledger untouched. Run them with `SBSM_TEST_POSTGRES=1 .venv/bin/python -m pytest tests/test_postgres.py` only when the intended private cloud configuration is present.

## Limitations and cutover

Render Free can sleep, restart, or exhaust shared workspace allowances. Neon Free also has storage and compute allowances. cron-job.org runs GET https://sbsm-trading.vercel.app/ping every minute on weekdays in the 09:05–09:16, 11:05–11:16, 13:05–13:16, 15:05–15:16 and 15:20–15:29 IST windows. Response history is enabled. There is no always-on guarantee. A sleeping service can miss TradingView's short delivery deadline. This is a cloud paper-testing setup; check readiness before enabling actual alerts. [Render limits](https://render.com/docs/free), [Neon plans](https://neon.com/docs/introduction/plans).

Broker access remains deferred. No candle-price fills or live broker orders are enabled. Telegram is enabled for the verified private chat; a connection test was delivered by the hosted outbox on 4 November 2026 (SENT, first attempt). Render-specific source verification uses its documented edge-overwritten CF-Connecting-IP header. A live forged-header probe was blocked at the edge; normal requests resolve through the configured mode. A genuine TradingView price alert verified Vercel receipt, worker processing and first-attempt Telegram delivery on 11 November 2026. This test did not create paper trades. Generic forwarded IP headers remain untrusted.

The cloud begins with a separate ₹500,000 paper ledger. The laptop ledger is not silently merged. Never direct the same intended portfolio's alerts to both ledgers. Use GETTING_STARTED.md to configure a first TradingView alert for receipt verification. Simulated fills still require broker quote readiness; do not treat successful receipt as execution readiness. A deliberate cutover remains necessary.

Render auto-deployment is off. Commit and push meaningful changes, then deploy a tested commit deliberately. Set Render's instance health-check path to `/health` (HTTP liveness, no database dependency). The supervisor uses the same path. Monitor `/health/ready` for operational health and the authenticated dashboard for broker/quote readiness. Access and authentication should be verified through the final Vercel address before relying on remote controls.

User walkthrough: [GETTING_STARTED.md](../GETTING_STARTED.md). The new private receiver URL is stored locally in ignored `work/durable-tradingview-webhook-url.txt`. The standard `work/tradingview-webhook-url.txt` now contains the same new receiver URL; the old Render URL is retained privately in `work/legacy-render-webhook-url.txt`. Receiver secrets are in ignored `.env.receiver` and Vercel production environment variables. The restricted receiver role cannot read or modify the fund ledger. These private files must never be committed.


The HEAD warm-up test verified an already warm server only. Subsequent scheduled
HEAD checks returned 502 while the service was unavailable. Repeated GET requests
replace the two isolated attempts per candle. A warm GET test verifies current
reachability and database/worker readiness. The next cold start and TradingView
delivery still require verification against the three-second webhook deadline.
Check scheduled history before the next signal window. Free hosting remains a
best-effort paper environment.

## Durable receiver verification

The Vercel `/receive/<private-token>` function inserts into Neon before returning 202. Render drains saved receipts in order through the existing idempotent engine. `/ping` checks the inbox database and wakes Render asynchronously, without waiting for Render to start. A cron-job.org test returned 200 in 433 ms. Scheduled windows above cover normal weekday sessions; special sessions require separate warm-up scheduling. Neither warm-ups nor the free receiver provide an absolute delivery guarantee.

A saved test receipt was processed after the worker was deployed, proving recovery of an already persisted receipt. A separate genuine TradingView test then reported successful webhook delivery, with a matching processed receipt and Telegram SENT record. Stock alert URL migration is a separate operational step; do not assume all existing alerts use the new destination until each has been checked.

All 20 existing stock alert destinations were saved to the new receiver. The older duplicate POLYCAB alert was stopped and the missing BSE alert created with the agreed 2H inputs (3, 0.16, 25, 25, 4, 30) and alert() calls only. All 20 intended symbols were observed active. The one-shot delivery test remains stopped, preserving its successful delivery log.

On 23 November 2026 the existing SBSM candle-close job was saved with the additional
09:05–09:16 IST weekday window (03:35–03:46 UTC). This supports next-session order
resumption at 09:15 without changing the other windows. Save confirmation was observed.


## Dhan automatic renewal — 23 November 2026 checkpoint

Backend commits `aa303ca` and `2efd605` are deployed on the existing free Render
service. Dhan credentials are encrypted in a dedicated SQL table; the encryption
key is a Render secret with an ignored local backup. The receiver role was
verified to have no read/update access to credentials or read access to encrypted
recovery records. Paper balances, reserves, realized P&L and position/order/fill/
ledger counts were unchanged across deployment and the live authentication test.

A separate enabled cron-job.org job, “SBSM Dhan renewal wake-up (every 6h)”, calls
the public Vercel `/ping` at 00:05, 06:05, 12:05 and 18:05 IST on all seven days.
Existing market warm-ups remain. The public ping and Render liveness returned
200 after deployment. This reduces idle-expiry risk; it is not an uptime guarantee.

**Live renewal is not yet verified.** Initial profile verification succeeded,
but the first live RenewToken response failed validation. A subsequent profile
call returned HTTP 400 / DH-906. The initial response was not retained for
recovery. The follow-up fix now encrypts and durably stores successful renewal
responses before interpretation and supports provisional response variants,
which still require a valid matching-account profile before use.

The worker records NEEDS_RECONNECT; its Telegram warning was sent. The user was
asked to generate a fresh token in Dhan Web, update SBSM_DHAN_ACCESS_TOKEN in
Render and redeploy. Do not retry the invalidated token or replay past expired
entries. Keep the existing nightly Codex reminders until a real replacement is
verified in Dhan, the worker, and dashboard; only then remove that automation.

Validation: 148 local tests passed. All nine isolated Postgres scenarios passed
across runs after rerunning a transient dropped-connection failure; the two Dhan
Postgres checks were repeated successfully after the response-recovery fix.
