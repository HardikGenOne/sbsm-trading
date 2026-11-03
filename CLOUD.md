# Cloud hosting and Telegram notifications

Status: deployment preparation and notification delivery are implemented. The user selected Render with a **free-only budget**, has a Telegram bot, and explicitly asked to wait before connecting Render. GitHub source publishing resumed on 4 November 2026; Render deployment and account setup remain on hold until the user resumes them. Nothing has been deployed remotely and no real Telegram message has been sent by the development checks. Telegram credentials and the destination still need local configuration.

## Free Render feasibility

The current SQLite-based stack must not be deployed unchanged to a free Render web service. Free services sleep after 15 minutes without inbound traffic and cannot attach persistent disks; local ledger files can disappear on restart or redeploy. An external cron ping does not provide durable storage or a guarantee of continuous processing. Render cron jobs also cannot access a web service's disk. [Render free-service limits](https://render.com/docs/free), [cron limitations](https://render.com/docs/cronjobs)

A free-tier experiment would first need a durable external database and a design that safely resumes processing after sleep, including delayed alert handling and notification delivery. That is a separate implementation and validation task; it cannot currently be described as the requested always-on simulation. No free Render blueprint is supplied that risks the existing paper ledger. Vercel is an optional frontend host, not a domain or a replacement for this continuous Python worker. The dashboard can use Render's HTTPS address if that hosting route is later made suitable.

The paid VPS instructions below are retained as an alternative reference only. They are not an approved spending plan. Do not provision paid services, connect Render or schedule deployment work until the user asks to resume.

## What runs continuously

The app runs as one supervised stack: API/dashboard, paper worker, and (when enabled) a separate Telegram dispatcher. The stack has one persistent SQLite volume and one replica. The supervisor initializes the schema before starting children, stops the stack if a child exits, and exits on repeated failed operational-health checks. Docker's `unless-stopped` policy restarts it. A broker login failure or a closed market is not a container-health failure: the app remains accessible while fills wait for market data.

The VPS Compose configuration uses Caddy for HTTPS, a private network for the app, a persistent ledger volume, persisted certificate storage, and bounded container logs. Port 8000 is not published. Only Caddy's fixed internal IP is trusted for forwarded client/protocol headers. Hostnames and TradingView source IPs are explicitly checked. Swagger is disabled in production. The global Caddy logger strips request metadata, and access logging stays disabled so credential-bearing webhook URLs are not recorded.

This is an always-on Python worker, so a static-site host or sleeping web tier does not satisfy the requirement. The existing Sites preview is not a cloud runtime for this stack.

## Choose a hosting target

An existing Linux VPS with Docker Compose is the most direct target for the supplied bundle. A small machine with about 2 GB RAM provides practical headroom for Python, the market calendar and the API/worker processes. AWS lists a Linux public-IPv4 2 GB Lightsail bundle at **US$12/month** before applicable taxes/extras; confirm region availability, backups and billing in the selected account before provisioning. [Official Lightsail bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)

Railway is an alternative and can upload a local directory without GitHub. It needs an attached persistent volume, a single replica, sleeping/serverless mode disabled, and an approved billing limit. Its Hobby plan has a **US$5/month minimum** with usage above the included allowance charged additionally; it is not a guaranteed $5 all-inclusive bill. Its volume permissions and proxy/healthcheck hostnames must be configured for this image before deployment. Railway documents root-owned volume mounts; the supplied image intentionally runs as a non-root user, so the chosen Railway setup needs an explicit permission solution rather than assuming the VPS Compose file applies unchanged. [Local source deployment](https://docs.railway.com/cli/up), [volumes and permissions](https://docs.railway.com/volumes), [pricing](https://docs.railway.com/pricing/plans)

Connecting hosting access, selecting a resource budget and confirming the target is still required. Do not create billable services or claim a public URL exists until deployment succeeds. GitHub source publishing has resumed separately from hosting; the VPS source-archive workflow does not need it.

## Prepare the VPS configuration locally

Once a real domain/subdomain has been selected:

```sh
.venv/bin/python scripts/cloud_config.py --domain paper.yourdomain.com
```

This creates private `.env.cloud` with new random admin/webhook credentials and refuses to overwrite an existing file. Review it locally. Configure optional Telegram using:

```sh
.venv/bin/python scripts/telegram_setup.py --env-file .env.cloud
```

Zerodha settings can remain empty while cloud/dashboard work proceeds. Do not turn on a fallback that fills orders at alert/candle prices. Cloud accessibility is separate from execution readiness.

## Build an immutable source bundle

After source changes are committed:

```sh
.venv/bin/python scripts/package_cloud.py
```

It writes `work/deploy/sbsm-COMMIT.tar.gz` from Git-tracked files and rejects tracked secrets/runtime databases. All actual configuration is separate. Transfer the archive and `.env.cloud` over authenticated SSH/SFTP to the selected server; never put credentials in Git or a public upload. Existing local paper data is not automatically migrated. Confirm whether the cloud starts a fresh paper ledger or receives a consistent backup before directing real alerts there. Do not keep two instances processing the same intended portfolio independently.

## VPS launch checklist

1. Use a supported Linux host with Docker Engine and the Compose plugin installed through official packages. Keep SSH access restricted to your management IP/key. Do not paste SSH private keys into chat.
2. Extract the archive in the server's dedicated project directory and place private `.env.cloud` at its root with permissions 0600.
3. Point the selected domain's DNS A record to the server's public IP. Open inbound TCP 80/443 and optionally UDP 443. Use a static public IP if a later live broker integration needs allowlisting.
4. From the extracted project directory run:

```sh
docker compose --env-file .env.cloud -f deploy/compose.yaml config --quiet
docker compose --env-file .env.cloud -f deploy/compose.yaml up --build -d
docker compose --env-file .env.cloud -f deploy/compose.yaml ps
```

5. Verify the actual public dashboard over HTTPS on a phone using mobile data, verify unauthenticated `/api/portfolio` returns 401, sign in with the **cloud** admin token, and confirm the cloud's intended ledger. Do not confuse it with the laptop's independent local ledger.
6. `/health/ready` should return 200 when API/database/worker are operational even without broker credentials. Check authenticated `/api/readiness` for market-data restrictions. Then verify the Telegram connection test and its sent status.
7. Update TradingView to `https://YOUR-DOMAIN/webhooks/tradingview/CLOUD-WEBHOOK-TOKEN` and verify one actual delivery against its alert log. The API verifies the official source-IP list after the trusted reverse proxy. Review that list when TradingView changes it. Do not send the token in chat or paste credential-bearing URLs into logs.
8. Reboot the VPS and confirm the stack restarts and preserves account balances, pending orders and notification state. Laptop shutdown must have no effect on this remote runtime.

Caddy obtains/renews public TLS certificates once DNS and public reachability are correct. [Caddy HTTPS documentation](https://caddyserver.com/docs/automatic-https) TradingView webhooks use ports 80/443 and a three-second receiver deadline; ingress commits before acknowledging and performs no broker/Telegram network calls. [TradingView webhook documentation](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)

## Telegram setup

1. Open Telegram and message the official `@BotFather` with `/newbot`. Create a dedicated SBSM notification bot.
2. Run `scripts/telegram_setup.py` with the intended local/cloud env file. Enter the bot token only in its hidden local prompt.
3. The helper verifies the bot with `getMe`, refuses to change a pre-existing webhook, and prints a random `/start` challenge. Send that message in your private conversation with your bot.
4. The helper matches the exact challenge and sender/private-chat identity, then asks you to enable messages to that verified destination. It stores the token and chat ID atomically with private file permissions. Group delivery is disabled unless explicitly configured separately.
5. A non-financial connection test can be sent after you confirm locally. Restart the stack so the notifier starts. In your actual dashboard, System health also has a **Send connection test** control once Telegram is configured.

The Bot API receives only the configured notification text and target chat. It uses outbound HTTPS `sendMessage`; no inbound Telegram webhook or trading commands are added. [Official Telegram Bot API](https://core.telegram.org/bots/api)

## Message types and delivery guarantees

- Entry accepted: stock, reserved budget, fund pool and available balance.
- Signal skipped: action, reason and sizing/cash snapshot when relevant.
- Buy/sell fill: shares, price, modeled costs, partial-fill progress and quote source.
- Sell fill: net realized P&L, updated pool and next allocation.
- Exit queued; pending-entry cancellation or expiry.
- Processing error/recovery transitions; missing/recovered worker heartbeat when the independent dispatcher remains running.

Messages are labelled **SBSM PAPER** and contain IST time and an event reference. They do not include broker credentials, admin tokens or webhook URLs. Normal unchanged health stays quiet; no periodic status or daily report is enabled by default.

The underlying financial state change and outbox insertion commit together. Delivery happens afterward without holding the account lock. Retries honor Telegram rate-limit delays, use bounded exponential backoff for transient failures, and stop on permanent errors or attempt exhaustion. Destination snapshots stop historical messages from being silently forwarded to a newly configured chat. Bot-token rotation for the same bot ID can continue delivery to the same chat.

Notification delivery is **at least once**, not exactly once: a timeout after Telegram accepted a message or a crash before saving acknowledgment can produce a duplicate. Use the event reference to identify it. A failed message never reverses or blocks a trade. During retry backoff, later notifications can arrive first. `/api/notifications` shows counts/status/error categories without exposing token or recipient ID. Failed messages can be retried explicitly through `/controls/notifications/{id}/retry` only for the same configured destination.

Whole-host/network/database failure can prevent the notification process itself from sending anything. Use the hosting provider's external uptime/error monitoring as well; an in-process bot cannot promise to alert on its own total outage.

## Persistence, backup and updates

New notification tables are introduced by additive migration `0002_notification_outbox`; existing cash, signals, orders, fills and ledger rows are preserved. Migrations run before children start. Test backup/restore before migrating any existing non-empty live paper ledger.

Create an integrity-checked online SQLite backup inside the persistent volume:

```sh
docker compose --env-file .env.cloud -f deploy/compose.yaml exec -T app python -m sbsm.backup
```

The tool reads committed WAL state consistently, writes a private snapshot and never overwrites an existing backup. It does not upload or automatically prune backups. Schedule it through the chosen host and retain encrypted copies outside the server; backups in the same volume do not protect against host/volume loss. Monitor disk space: quote snapshots and backups accumulate. Do not delete the Compose volumes or use `down -v` during an update.

To update, back up first, transfer the new committed source archive while preserving `.env.cloud`, and run Compose `up --build -d` again. Keep the source revision and a matching backup for recovery. An older image must not be blindly pointed at a newer migrated database. One replica is required for this local-volume SQLite architecture.

## Verification limits

The Python test suite covers financial invariants, notification rollback/retry/restart, destination isolation, host/IP checks, production validation, migrations and backup integrity. Local supervisor/HTTP behavior can be checked without a broker. This machine has no Docker daemon, so a real image build, full Compose run, DNS/TLS, remote reboot, phone access, Railway volume permissions and actual Telegram delivery still require the selected hosting/bot accounts. No remote deployment is claimed by packaging source or writing these files.
