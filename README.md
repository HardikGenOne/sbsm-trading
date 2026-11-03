# SBSM equity webhook package

The Python paper-trading engine and local dashboard are now in this folder. Start with `.venv/bin/python scripts/run_local.py`, then open http://127.0.0.1:8000/ to explore the isolated demo sandbox or sign in to your actual paper account. See [SERVER.md](SERVER.md) for installation, startup, market-data configuration, API endpoints, and simulation assumptions. This document retains the Pine alert setup and signal contract.

The dashboard is hosted at [sbsm-trading.vercel.app](https://sbsm-trading.vercel.app), backed by Render Free and durable Neon Postgres. See [deploy/HOSTED.md](deploy/HOSTED.md) for verified setup and limits. Telegram is enabled and its hosted connection test succeeded. Broker access remains pending; free hosting does not guarantee continuous execution.

## Files

- `SBSM_equity_alerts.pine`: Pine v5 strategy with structured signal alerts.
- `signal.schema.json`: JSON Schema for the FastAPI ingress boundary.
- `examples/enter-long.json` and `examples/exit-long.json`: paired sample messages.

## TradingView setup

1. Paste the Pine file into Pine Editor, save, and add it to the chart.
2. For webhooks, open one approved NSE equity on standard candlesticks, timeframe **2H / 120 minutes**. The top-right **Webhook eligibility** row must show **READY**. Other charts display a **BLOCKED** webhook reason, but standard charts still run the strategy backtest and show trades when qualifying signals occur. NIFTY 50 can be used for strategy review; it cannot send execution webhooks in this per-stock setup. Select, for example, `NSE:ICICIBANK` or `NSE:SBIN` for equity alerts. A separate **Strategy backtest** row distinguishes backtest activity from webhook eligibility.
3. Keep strategy recalculation on bar close. Do not enable recalculation on every tick or after fills. Keep fills-on-bar-close disabled for the reference backtest.
4. Set the desired SBSM inputs. Defaults match the supplied strategy.
5. Create an alert for this strategy, selecting **alert() function calls only**. The script generates the JSON message; do not paste a separate static buy/sell template or use order-fill placeholders.
6. Set the HTTPS webhook URL supplied by the server. Do not include broker credentials in the Pine code or payload.
7. Repeat for each of the 20 stocks. Keep only one active alert per stock/configuration. Enable TradingView two-factor authentication and check webhook availability/alert limits in the selected subscription before buying it.
8. Check the first real signal in TradingView's alert log and the server's paper ledger. Do not enable live execution during this check.

The strategy's emulator order alerts are disabled as an extra safeguard. Historical signals draw chart markers but do not send historical orders to the server. Alerts are snapshots: recreate them after changing code, parameters, symbol, or timeframe. Coordinate replacements with the server; changing configuration creates a different strategy instance and must not orphan existing server positions.

## Preserved trading semantics

- Long only; sell means exit, never open a short.
- The previous-close breakout, extra history shift in the breakout range, bullish Supertrend, WMA, gap measured from previous high, and cooldown on both buys and sells are preserved.
- The current bar's open is used for the gap check, but the event is emitted at that bar's confirmed close. No intrabar entry or gap-at-open execution has been introduced.
- `lastSignalBar` starts at zero, preserving the original startup cooldown. A sell while flat updates trend/cooldown without emitting an unpaired exit.
- Removed the expired backtest date window, short option, custom P&L arrays, and hardcoded P&L multiplier. No stop-loss, target, daily-loss exit, or end-of-day exit is added.
- Chart Strategy Tester uses a single-stock 100% allocation for reference only. It does not reproduce the shared 12% portfolio or server fills. Its orders normally fill on the next available tick, while the webhook reports the preceding signal event.

## Protocol

`schema_version` identifies the JSON contract. `strategy_version` identifies signal logic. `strategy_instance_id` combines strategy version, exchange/symbol, timeframe, and all signal parameters. Change the strategy version whenever changing signal logic or state semantics.

`event_id` = instance ID + signal-bar close timestamp + action. Enforce a database uniqueness constraint scoped to the server trading account/environment. Repeated delivery of an event is a duplicate, even if `emitted_at_ms` differs.

`trade_cycle_id` = instance ID + entry signal-bar close timestamp. The matching exit retains this exact value. Treat IDs as opaque, case-sensitive strings; do not split on punctuation. In particular, `M&MFIN` is a literal symbol.

All timestamps are Unix epoch milliseconds. `signal_bar_open_ms` and `signal_bar_close_ms` describe the chart bar, not the previous candle used by setup calculations. `emitted_at_ms` is the Pine execution clock, not an exchange execution timestamp. The server records its own durable receipt time and sequence for FIFO.

The 120-minute chart can have a shorter final session bar. Use its actual `time_close`; do not derive close time by adding two hours to the open time. A session-close signal may reach the server after execution is possible that day.

`reference_price` is a numeric signal-bar close for auditing. It is not a requested fill price. Quantities, allocation, broker product, account, and paper/live mode are intentionally server-owned.

## Required server processing

1. Authenticate/validate webhook origin at the ingress independently of JSON validation. These messages carry no credential or signature. Never treat a valid payload as proof that TradingView sent it.
2. Validate schema, allowlisted symbols, approved version/configuration, timestamp ordering, freshness policy, exchange/timeframe, and exact ID construction. Compare the complete approved `config_key`, not just the version.
3. Persist the event and durable FIFO sequence before acknowledging. Deduplicate by event ID. If the same ID arrives with different substantive fields, quarantine it instead of replacing history.
4. For entries, serialize allocation per account and atomically reserve 12% of the pool, including charge/slippage allowance. Pool = initial funds + realized net P&L; cash availability is a separate check. Round down to whole shares. Skip insufficient budgets/cash permanently and save the decision.
5. Only one open/pending position per stock, including across strategy configurations. Match exits to the exact cycle and close only its recorded remaining quantity. Never interpret an unmatched exit as a new short. If an entry is pending, cancel/reconcile it before closing its fills.
6. A skipped entry still has a TradingView cycle; record its later exit as an unmatched/skipped-cycle event without sending an order. Pine cannot know whether our server accepted an entry.
7. Define out-of-session entry and exit policy before execution. Preserve valid exit intent for an owned position when the market is closed; do not discard it because an entry freshness rule expires. Do not silently queue late entries for the next open.
8. Recover/reconcile broker orders before retrying uncertain submissions. Signal IDs deduplicate webhooks but do not by themselves make broker submissions exactly once.

Starting/recreating an alert while its reconstructed Pine state is already long does not send a catch-up entry. A later sell may reference a historical cycle unknown to the server; record it without trading. Chart-history changes, data corrections, and settings changes can alter reconstructed state. Never use Pine state to overwrite broker-confirmed positions; investigate an unmatched exit while any position in that stock is open.

## Verification limits and rollout

The package includes a JSON Schema and paired payload fixtures with fictional prices. JSON parsing, required-field coverage, symbol/ticker matching, timestamp ordering, ID construction, action/reason pairing, and shared entry/exit cycle checks passed locally. A full JSON Schema validator and a Pine compiler are unavailable locally; schema-validator execution, compilation in TradingView, and a real alert-log check are still required. Do not represent static file checks as a successful TradingView compile or webhook end-to-end test.

Compare markers candle by candle with the original strategy using the same inputs, dataset, and an unrestricted date range. Original expired date filtering and short trades are deliberately absent. Check buy/sell timestamps and cooldown behavior rather than the removed custom P&L table. Then observe one entry and matching exit in paper mode, test duplicate delivery, and check a skipped-entry cycle produces no broker order on exit.

## Official references

- [TradingView Pine v5 alerts](https://www.tradingview.com/pine-script-docs/v5/concepts/alerts/)
- [TradingView Pine v5 strategies](https://www.tradingview.com/pine-script-docs/v5/concepts/strategies/)
- [TradingView webhook configuration](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)

Start here: [TradingView, website and Telegram tutorial](GETTING_STARTED.md).
