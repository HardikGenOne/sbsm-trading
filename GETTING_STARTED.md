# SBSM: TradingView, dashboard and Telegram

## Current setup

Dashboard: https://sbsm-trading.vercel.app. Backend: https://sbsm-paper.onrender.com.
The cloud paper account starts with ₹5,00,000 and sizes each new entry at 12% of initial capital plus net realized P&L. Unrealized gains do not increase the sizing pool. The dashboard's demo sandbox is separate from your actual account.

Telegram is configured on the hosted server for the user's verified private chat with **@Trading_signal_alerts_bot**. Its token and chat ID are stored in private configuration, never in this guide or Git. A hosted connection test was confirmed SENT on 4 November 2026, on its first attempt. Check System health for subsequent delivery status. Telegram is for notifications; it does not accept trading commands.

Broker market-data access is still deferred. Signals can be received and recorded, but simulated fills require genuine fresh quotes: the TradingView candle/reference price is never substituted. A BUY notification saying **ENTRY ACCEPTED** means a budget was reserved, not that shares filled. With no quotes, an accepted entry normally expires after 60 seconds and releases its reservation. Unfilled exits remain pending until eligible quotes and an open market are available.

## 1. Prepare TradingView

1. Enable TradingView two-factor authentication. Webhooks require it. Choose a subscription that supports webhook notifications and enough active **technical** alerts for your stocks; this setup uses one strategy alert per stock, carrying both entry and exit events. For all 20 stocks, you need 20 such alert slots. Confirm current features on TradingView's pricing page before purchasing.
2. Start with **NSE:ICICIBANK**, standard candlesticks, **2H / 120 minutes**. Use the NSE listing, not an index, futures, options, another exchange or Heikin Ashi/Renko chart. Check that the data is realtime; delayed chart signals can be rejected as stale by the server.
3. Open Pine Editor. Paste the complete contents of **SBSM_equity_alerts.pine** from this project, save it, and add it to the chart. Use the supplied version rather than the original NIFTY script or a generic BUY/SELL alert.
4. Keep these approved inputs: breakout 3, gap 0.16%, cooldown 25 bars, Supertrend ATR 25/factor 4, WMA 30. The server checks this configuration. A setting change requires coordinated server configuration and newly created alerts.
5. The script's chart panel must show **READY - alerts enabled**. This confirms chart eligibility only; it does not verify broker readiness or server delivery.

Official references: [webhook requirements](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/), [Pine alerts](https://www.tradingview.com/pine-script-docs/concepts/alerts/), [plans](https://www.tradingview.com/pricing/).

## 2. Create the stock's alert

1. Open **Create alert** on that chart.
2. For **Condition**, select **SBSM Equities 2H | Webhook v1.0.0**. Select the event option labelled **alert() function calls only** (wording can vary slightly). The script deliberately disables order-fill notifications; selecting order fills will not produce the required webhook events.
3. The script already sends once at a confirmed bar close. If a frequency option is available, use **Once per bar close**. Do not replace the generated JSON with `BUY`, `SELL`, TradingView order placeholders, or a manually maintained JSON template. The running `alert()` call supplies the complete dynamic message, including stock, action, timestamp and trade-cycle ID.
4. Enable **Webhook URL** in Notifications. Paste the complete private URL from **work/tradingview-webhook-url.txt**. Its shape is `https://sbsm-paper.onrender.com/webhooks/tradingview/<private-webhook-token>`. Use the backend address, not Vercel. Do not use the Telegram bot token or the dashboard admin token here. Do not publish this URL.
5. Name it **SBSM · ICICIBANK · 2H**, check its expiration, and create it.
6. Repeat for each approved NSE stock you want to monitor. Changing the chart after creating one alert does not make that alert monitor a different stock. Keep only one active copy of each stock's alert.

Approved symbols: POLYCAB, ADANIENT, APARINDS, APLAPOLLO, BAJAJ_AUTO, BEL, BHEL, BSE, CANBK, CONCOR, EICHERMOT, FORTIS, HINDCOPPER, ICICIBANK, LAURUSLABS, M&MFIN, MCX, NTPC, SBIN, ZYDUSLIFE.

TradingView runs active alerts on its servers, so its chart tab and your laptop can be closed. An alert stores a snapshot of the script, inputs, symbol and timeframe when created. After editing those, delete/recreate the affected alert. Historical chart BUY/EXIT markers do not send a backlog of webhooks. New alerts fire on future realtime qualifying events only. A newly connected server may receive an EXIT for a historically reconstructed cycle it never owned; **NO_MATCHING_POSITION** is expected and does not open a short.

## 3. Verify the first real event

The security check requires both the private webhook credential and TradingView's official source IP. Render's public edge supplies the sender address through its documented overwritten CF-Connecting-IP header. Generic forwarded IPs are not trusted. [Render proxy documentation](https://render.com/articles/host-pocketbase-on-render).

When a genuine signal fires, check TradingView's alert log and its **Webhook status** column. Successful durable receipt should be HTTP **202**. Then find the matching stock/action in **Signal inbox** and open its trace to see the actual decision, orders and fills. Check Telegram for the corresponding decision/fill message. A signal can legitimately be skipped; receiving a webhook does not guarantee a fill.

Do not trigger fake strategy trades just to test Telegram. System health has an independent connection-test button. The first genuine TradingView delivery is still required to prove the complete route.

Troubleshooting: **401** means an incorrect webhook token; **403** means sender verification failed; **422** means the payload/configuration was rejected; **429** means the ingress rate limit was exceeded; **503** means receipt was not safely acknowledged. A timeout/cold start is a hosting-delivery issue. TradingView's webhook deadline is three seconds; sleeping Render Free instances can miss it. Opening the dashboard is not an uptime guarantee. Do not assume every failed delivery will be retried.

## 4. Use the website

Open the dashboard on your laptop or phone. Click **Open my paper account** and use **SBSM_ADMIN_TOKEN** from the private cloud file **.env.render**. Use the cloud token, which differs from the laptop's local .env. Keep it private. If you see **Demo sandbox**, you are viewing illustrative browser-only data; select **My paper account** for actual records.

| View | What to use it for |
| --- | --- |
| Overview | Fund pool, free cash, next 12% budget and worker/account state. |
| Signal inbox | Confirm arrivals and inspect their decision-to-fill trace. |
| Order book | See reserved, pending, partial, expired and completed orders. |
| Execution fills | Actual paper execution prices, quantities and modeled charges. |
| Positions | Current simulated holdings; Request exit queues a manual paper exit. |
| Skipped signals | Permanent rejection records, including insufficient funds. |
| Trade history | Closed trade cycles and realized results. |
| Fund ledger | Reservations, fills, releases, settlement and balance changes. |
| System health | Feed/worker readiness and Telegram enabled/sent/failed counts. |

**Pause entries** blocks new admissions. It does not cancel accepted orders or block exits. Cancel a pending entry explicitly if needed. **Request exit** is an operator action; normal strategy exits come from TradingView. There is no automatic stop-loss, daily-loss exit, or Telegram order command.

## 5. Understand messages and money

The pipeline is: TradingView signal → durable signal record → FIFO admission → cash reservation → fresh quote and paper fill → updated ledger → Telegram delivery. Network delivery runs separately and cannot roll back a trade. Messages include **SBSM PAPER**, stock, event reference and IST time. Retries can occasionally produce duplicates; compare event references.

At ₹5,00,000 the budget is ₹60,000 per new trade. Eight unfilled full-budget reservations use ₹4,80,000; a ninth is skipped with ₹20,000 free. A skipped signal is not replayed when cash becomes available. Realized net gains/losses change future budgets; at a realized pool of ₹10,00,000 the next allocation is ₹1,20,000. Sizing includes whole-share and modeled-charge constraints. Sale proceeds follow the configured settlement hold before reuse.

Free Render/Neon allowances and sleep/restarts remain operational limits. The database survives Render redeploys, but this free deployment is not guaranteed continuous execution. Broker feed readiness and the first genuine TradingView webhook must be verified before relying on the simulation.
