# SBSM NIFTY 1-minute intraday backtest

This is an offline, long-only, price-based backtest. It does not connect to SBSM's
paper server, a broker, or Telegram. It does not change the live 2H stock strategy.

## Run locally

Install Python 3.10+ and the two dependencies:

```sh
pip install numpy pandas
python sbsm_intraday.py --csv NIFTY50_SPOT_1min_2024-01-01_to_2026-11-25.csv --output results
```

Download the Python file and keep it beside your CSV, or provide the CSV's full
path. All parameters can be changed using command-line options (`--help`).

## Google Colab

The easiest route is to upload `SBSM_1min_intraday.ipynb` to Google Colab, run its
cells in order, edit the settings cell and upload the NIFTY CSV. The notebook is
self-contained, with the same backtester code embedded. A Colab runtime was not
available for interactive widget testing here; Python cells were syntax-checked
and the underlying runner was exercised on the full CSV.

Alternatively, in a new notebook, run:

```python
!pip -q install numpy pandas
from google.colab import files
files.upload()  # Select sbsm_intraday.py first.
```

Then run the following in another cell. Select your NIFTY CSV when prompted:

```python
%run sbsm_intraday.py --upload --download
```

`%run` executes in the notebook kernel so the upload/download widgets work. The
result ZIP contains the trades, summary, equity curve and per-bar indicators.

## Signal rules preserved

The reference is `SBSM_equity_alerts.pine` in the project, version 1.0.0, using
the user's latest inputs. Default values are:

| Parameter | Value |
| --- | ---: |
| Breakout lookback | 3 |
| Maximum upward gap above prior high (%) | 0.16 |
| Signal cooldown, including exits (bars) | 25 |
| Supertrend ATR period | 25 |
| Supertrend factor | 4 |
| WMA period | 30 |

The script runs directly on 1-minute candles. It does not form 2H candles.

At signal bar `t`, the breakout reference is the highest high / lowest low of
bars `t-lookback-1` through `t-2`. This deliberately reproduces
`ta.highest(high[1], lookback)[1]`, not the more common one-bar shift.

BUY requires the previous close above that breakout high, previous Supertrend
direction negative (bullish), previous close above its WMA, current open no more
than 0.16% above the previous high, a non-long trend state, and the cooldown.
The gap condition is one-sided: a negative gap is not rejected by an absolute-gap
filter. SELL requires the previous close below the breakout low, a non-short
trend state and the cooldown. It does not require a bearish Supertrend or an MA
cross. No stop-loss or profit target is added.

ATR uses Wilder RMA with an SMA seed; WMA gives the newest close the greatest
weight. Signal states update independently of actual positions. Initial flat
SELL setups update the cooldown. Trend state, cooldown, ATR, WMA and the active
signal cycle continue across days and are not reset by forced square-off.
Consequently, forced flat does not automatically enable a fresh BUY next morning.

## Fill timing and intraday modification

Ordinary BUY/SELL signals are evaluated on a confirmed signal candle, and fill
at the **next available same-day candle's open**, matching the source Pine's
`process_orders_on_close=false`. They do not fill at the breakout candle's open
or the signal candle's close. Position pyramiding is disabled.

The one requested execution change is intraday square-off: close a remaining
position at **15:15 IST using the 15:14 candle's close**. That candle completes at
15:15; the script never uses the 15:15 candle's later close. This is equivalent
to `strategy.close_all(immediately=true)` on the candle ending at 15:15. No new
BUY can fill at or after the cutoff; unfilled BUYs cannot carry overnight.

Known early-closing sessions on 2 March / 18 May 2024 square off at 12:30, and
21 October 2025 at 14:45. It is impossible to exit at 15:15 when the session has
already closed. Other known special-session windows are retained for indicator
history; a session that starts after the 15:15 cutoff cannot enter trades.
Update the session map for additional dates using `--sessions-json` with a JSON
object of date -> intervals, e.g. `{"2027-01-01": [["09:15", "12:00"]]}`.
These examples are configuration syntax, not claims about future exchange hours.

If the CSV omits a required square-off candle, the backtest fails rather than
inventing an exit or carrying a position overnight. A dataset ending partway
through its final day leaves a position or pending order open in the summary;
it does not assume that the last data row was market close.

## Data and accounting assumptions

- Input timestamps denote candle opens. Timezone-aware data is converted to IST;
  timezone-naive timestamps are interpreted as IST. Duplicate timestamps fail.
- Post-close records are excluded from regular sessions; known Muhurat and split
  sessions are handled separately. Never forward-fill missing candles.
- Prices are rounded to two decimals in memory to remove the known tiny Dhan
  floating-point artifacts. The original CSV is not modified. `--price-decimals -1`
  disables rounding, but invalid OHLC envelopes then fail validation.
- Volume is unused. This is NIFTY spot, not a simulation of options/futures fills.
- Sizing defaults to one index unit with point value one. P&L is therefore index
  points, not an options return. This differs from the source Pine's 100% equity
  order sizing and the live stock server's 12% allocation; neither sizing changes
  the signal formulas. Quantity and point value are configurable.
- Default commission and slippage are zero, as in the source Pine. Add costs
  using `--commission-bps` per side and `--slippage-points` per side. Positive
  slippage increases buys and decreases sells.
- Drawdown is measured on bar-close marked equity, not TradingView's intrabar
  high/low worst-case drawdown. Initial capital is an equity reporting baseline;
  the fixed-unit research simulation does not model margin, market depth or cash
  admission. Do not interpret the percentage return as a leveraged instrument return.

## Outputs and comparison limits

`trades.csv`, `events.csv`, `equity.csv`, `monthly.csv` (when trades exist),
`bars_and_signals.csv`, and `summary.json` are bundled into a ZIP. The last file
records parameters, input checksum, session filtering and open/pending state.

`SBSM_intraday_comparison.pine` is an optional, separate TradingView comparison
strategy with the same intraday addition and one-unit sizing. It contains no
webhooks. Use standard NIFTY 1-minute candles and the same settings. This supplied
comparison script has not been compiled in TradingView in this task.

Python logic is covered by indicator, strict-boundary, no-lookahead, cost,
next-open, square-off, early-close and truncated-data tests. **Actual bar-for-bar
TradingView parity has not been established.** Dhan and TradingView can differ
in prices, session bars, loaded history and indicator/state warm-up. Compare
`bars_and_signals.csv` with TradingView's exported indicator/signal plots before
claiming identical trades or performance. The original overnight 2H strategy's
58% win rate / 2.1 PF is not transferable to this changed timeframe and exit rule.

Reference documentation:
- https://www.tradingview.com/pine-script-docs/concepts/strategies/
- https://www.tradingview.com/support/solutions/43000634738-supertrend/
- https://nsearchives.nseindia.com/content/circulars/MSD60677.pdf
- https://nsearchives.nseindia.com/content/circulars/MSD61893.pdf
- https://nsearchives.nseindia.com/content/circulars/CMTR70319.pdf


## Optional overnight mode and execution date range

To remove only forced liquidation while retaining the existing 15:15 entry cutoff, use:

```sh
python sbsm_intraday.py --csv NIFTY.csv --start 2026-10-12 --no-force-square-off --output overnight_results
```

This mode retains entries before 15:15, carries positions overnight, and fills
SBSM exits at the next available bar open, including the next session when a
signal occurs on the last bar. It changes only the forced-exit policy. Exits occur
only on SBSM SELL signals. Positions still open when the data ends remain open;
no final artificial exit is added. The 15:15 policy remains the default unless
this flag is explicitly supplied. In the notebook, set `force_square_off=False`
and keep `entry_cutoff=True`.

For unrestricted original Pine-style overnight execution, also pass
`--no-entry-cutoff` (or set `entry_cutoff=False` in the notebook). That explicitly
allows late-session BUY signals and next-session-open BUY fills. A comparison
that changes only forced exits should keep the entry cutoff enabled.

`--start` restricts execution, not calculation: all earlier CSV data still warms
ATR/WMA and reconstructs Pine trend/cooldown/cycle state. The portfolio starts
flat at the requested execution date; no trade opened before that date is
imported. This makes the requested period a fresh execution test with warm
indicators. Ordinary session filtering still applies in either mode.
