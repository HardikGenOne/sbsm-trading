"""Standalone SBSM 1-minute NIFTY spot backtest. Dependencies: numpy, pandas.

Local: python sbsm_intraday.py --csv NIFTY.csv --output results
Colab: python sbsm_intraday.py --upload --download
No broker connection, credentials, database, or live orders are used.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
from datetime import time
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

TZ = "Asia/Kolkata"
# Announced special sessions in the supplied dataset. [open, close) intervals.
SPECIAL = {
    "2024-03-02": [("09:15", "10:00"), ("11:30", "12:30")],
    "2024-05-18": [("09:15", "10:00"), ("11:30", "12:30")],
    "2024-11-01": [("18:00", "19:00")],
    "2025-10-21": [("13:45", "14:45")],
    "2026-11-08": [("14:20", "15:30")],
}


@dataclass(frozen=True)
class Config:
    lookback: int = 3
    gap_limit: float = 0.16
    noise_bars: int = 25
    atr_period: int = 25
    st_factor: float = 4.0
    wma_period: int = 30
    square_off: str = "15:15"
    quantity: float = 1.0
    point_value: float = 1.0
    initial_capital: float = 500000.0
    commission_bps: float = 0.0
    slippage_points: float = 0.0
    price_decimals: int = 2  # -1 preserves raw Dhan floating-point artifacts.

    def __post_init__(self):
        if min(self.lookback, self.atr_period, self.wma_period) < 1 or self.noise_bars < 0:
            raise ValueError("Indicator lengths must be positive; cooldown cannot be negative")
        if self.st_factor <= 0 or self.gap_limit < 0 or min(self.quantity, self.point_value, self.initial_capital) <= 0:
            raise ValueError("Invalid factor, allocation or capital")
        if min(self.commission_bps, self.slippage_points) < 0 or self.price_decimals < -1:
            raise ValueError("Costs must be nonnegative; price-decimals must be -1 or greater")
        if not all(np.isfinite(value) for value in (self.st_factor, self.gap_limit, self.quantity, self.point_value, self.initial_capital, self.commission_bps, self.slippage_points)):
            raise ValueError("Numeric parameters must be finite")
        cutoff = time.fromisoformat(self.square_off)
        if not time(9, 15) < cutoff <= time(15, 30) or cutoff.second:
            raise ValueError("Square-off must be a minute boundary after 09:15 and at/before 15:30")


def load_csv(path: Path, cfg: Config, overrides=None):
    data = pd.read_csv(path)
    data.columns = [str(c).strip().lower().lstrip("\ufeff") for c in data.columns]
    missing = set(("open", "high", "low", "close")) - set(data.columns)
    if missing:
        raise ValueError(f"Missing CSV columns: {sorted(missing)}")
    stamp = next((x for x in ("datetime_ist", "datetime", "timestamp_unix", "timestamp", "time", "date") if x in data.columns), None)
    if stamp is None:
        raise ValueError("CSV requires datetime_ist, datetime, timestamp_unix, timestamp, time or date")
    if pd.api.types.is_numeric_dtype(data[stamp]):
        unit = "ms" if data[stamp].abs().max() > 100_000_000_000 else "s"
        index = pd.DatetimeIndex(pd.to_datetime(data[stamp], unit=unit, utc=True)).tz_convert(TZ)
    else:
        index = pd.DatetimeIndex(pd.to_datetime(data[stamp], format="mixed"))
        index = index.tz_localize(TZ) if index.tz is None else index.tz_convert(TZ)
    if index.hasnans or index.has_duplicates or (index.second != 0).any() or (index.microsecond != 0).any():
        raise ValueError("Timestamps must be unique, valid minute-opening times")
    data.index = index
    data = data.sort_index()
    data = data[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(data.to_numpy()).all() or (data <= 0).any().any():
        raise ValueError("All OHLC prices must be positive and finite")
    raw_bad = ((data.high < data[["open", "close"]].max(axis=1)) |
               (data.low > data[["open", "close"]].min(axis=1)) | (data.high < data.low))
    if cfg.price_decimals >= 0:
        data = data.round(cfg.price_decimals)
    bad = ((data.high < data[["open", "close"]].max(axis=1)) |
           (data.low > data[["open", "close"]].min(axis=1)) | (data.high < data.low))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} OHLC inconsistencies remain; inspect source data or choose price-decimals=2")
    session_map = dict(SPECIAL)
    if overrides:
        session_map.update(overrides)
    include, cutoffs, session_ends = [], [], []
    for stamp in data.index:
        date = stamp.strftime("%Y-%m-%d")
        intervals = session_map.get(date, [("09:15", "15:30")])
        windows = [(pd.Timestamp(date + " " + a, tz=TZ), pd.Timestamp(date + " " + b, tz=TZ)) for a, b in intervals]
        if any(b <= a for a, b in windows) or not windows:
            raise ValueError(f"Invalid session override for {date}")
        include.append(any(a <= stamp < b for a, b in windows))
        end = max(b for _, b in windows)
        cutoffs.append(min(pd.Timestamp(date + " " + cfg.square_off, tz=TZ), end))
        session_ends.append(end)
    data["cutoff"] = cutoffs
    data["session_end"] = session_ends
    before = len(data)
    data = data.loc[include].copy()
    if data.empty:
        raise ValueError("No valid session candles remain")
    return data, {"input_rows": before, "retained_session_rows": len(data),
                  "excluded_outside_session_rows": before-len(data), "raw_ohlc_inconsistencies": int(raw_bad.sum()),
                  "round_prices_to_decimals": cfg.price_decimals}


def pine_rma(values, length):
    """Wilder RMA, SMA seed after length valid observations; not EMA-from-first."""
    result = np.full(len(values), np.nan)
    seed, previous = [], np.nan
    alpha = 1.0 / length
    for i, value in enumerate(values):
        if np.isnan(value):
            result[i] = previous
            continue
        if np.isnan(previous):
            seed.append(value)
            if len(seed) == length:
                previous = sum(seed) / length
        else:
            previous = alpha * value + (1.0-alpha) * previous
        result[i] = previous
    return result


def pine_supertrend(high, low, close, period, factor):
    prior_close = np.r_[np.nan, close[:-1]]
    true_range = np.maximum(high-low, np.maximum(np.abs(high-prior_close), np.abs(low-prior_close)))
    true_range[0] = high[0]-low[0]  # ta.tr(true), used by ta.atr().
    atr = pine_rma(true_range, period)
    upper, lower = np.full(len(close), np.nan), np.full(len(close), np.nan)
    trend, direction = np.full(len(close), np.nan), np.ones(len(close), dtype=int)
    for i in range(len(close)):
        basic_upper = (high[i]+low[i])/2 + factor*atr[i]
        basic_lower = (high[i]+low[i])/2 - factor*atr[i]
        previous_upper = 0.0 if i == 0 or np.isnan(upper[i-1]) else upper[i-1]
        previous_lower = 0.0 if i == 0 or np.isnan(lower[i-1]) else lower[i-1]
        pc = np.nan if i == 0 else close[i-1]
        lower[i] = basic_lower if basic_lower > previous_lower or pc < previous_lower else previous_lower
        upper[i] = basic_upper if basic_upper < previous_upper or pc > previous_upper else previous_upper
        if i == 0 or np.isnan(atr[i-1]):
            direction[i] = 1
        elif trend[i-1] == previous_upper:
            direction[i] = -1 if close[i] > upper[i] else 1
        else:
            direction[i] = 1 if close[i] < lower[i] else -1
        trend[i] = lower[i] if direction[i] == -1 else upper[i]
    return atr, trend, direction


def calculate_signals(data, cfg):
    """Literal signal timing/indexing of SBSM_equity_alerts.pine sections 6–7."""
    result = data.copy()
    h, l, c = (data[key].to_numpy(dtype=float) for key in ("high", "low", "close"))
    atr, st, direction = pine_supertrend(h, l, c, cfg.atr_period, cfg.st_factor)
    result["atr"], result["supertrend"], result["st_direction"] = atr, st, direction
    weights = np.arange(1.0, cfg.wma_period+1)
    wma = np.full(len(c), np.nan)
    if len(c) >= cfg.wma_period:
        wma[cfg.wma_period-1:] = np.convolve(c, weights[::-1], mode="valid") / weights.sum()
    result["wma"] = wma
    # ta.highest(high[1], lookback)[1] excludes BOTH the current bar
    # and the immediately previous breakout candle. Do not change shift(2).
    result["previous_breakout_high"] = data.high.shift(2).rolling(cfg.lookback, min_periods=1).max()
    result["previous_breakout_low"] = data.low.shift(2).rolling(cfg.lookback, min_periods=1).min()
    result["setup_buy"] = ((data.close.shift(1) > result.previous_breakout_high) &
                           (result.st_direction.shift(1) < 0) & (data.close.shift(1) > result.wma.shift(1)))
    result["setup_sell"] = data.close.shift(1) < result.previous_breakout_low
    result["gap_pct"] = (data.open-data.high.shift(1))/data.high.shift(1)*100
    trend_state, last_signal_bar, active_cycle = 0, 0, False
    buy, sell, states, last, flat_sell = [], [], [], [], []
    for i, row in enumerate(result.itertuples()):
        clean = i-last_signal_bar >= cfg.noise_bars
        b = bool(row.setup_buy and row.gap_pct <= cfg.gap_limit and trend_state != 1 and clean)
        s = bool(not b and row.setup_sell and trend_state != -1 and clean)
        exit_signal = s and active_cycle
        if b:
            trend_state, last_signal_bar, active_cycle = 1, i, True
        elif s:
            trend_state, last_signal_bar = -1, i
            if active_cycle:
                active_cycle = False
        buy.append(b); sell.append(exit_signal); flat_sell.append(s and not exit_signal)
        states.append(trend_state); last.append(last_signal_bar)
    result["buy_signal"], result["sell_signal"] = buy, sell
    result["flat_sell_state_update"] = flat_sell
    result["trend_state"], result["last_signal_bar"] = states, last
    return result


def simulate(signals, cfg):
    """Ordinary market orders: next available same-day bar open.

    Forced exit: 15:14 candle CLOSE, timestamped 15:15. Earlier official
    session closes use their last minute CLOSE. No last-row forced liquidation.
    """
    trades, events, equity = [], [], []
    position, pending, realized = None, None, 0.0
    previous_day = None
    multiplier = cfg.quantity*cfg.point_value

    def fee(price):
        return price*multiplier*cfg.commission_bps/10000

    def close_position(stamp, raw_price, reason, signal_stamp=None):
        nonlocal position, realized
        fill = raw_price-cfg.slippage_points
        gross = (fill-position["entry_price"])*multiplier
        costs = position["entry_fee"]+fee(fill)
        net = gross-costs
        trades.append({"entry_signal_bar": position["signal_bar"], "entry_time": position["entry_time"],
                       "entry_price": position["entry_price"], "exit_signal_bar": signal_stamp,
                       "exit_time": stamp, "exit_price": fill, "exit_reason": reason,
                       "quantity": cfg.quantity, "point_value": cfg.point_value,
                       "price_points": fill-position["entry_price"], "gross_pnl": gross, "commission": costs,
                       "net_pnl": net, "holding_minutes": (stamp-position["entry_time"]).total_seconds()/60})
        realized += net
        position = None

    for stamp, row in signals.iterrows():
        day = stamp.date()
        if previous_day is not None and day != previous_day:
            if position is not None:
                raise ValueError(f"Missing square-off candle on {previous_day}; refusing an invented exit or overnight carry")
            if pending:
                events.append({"time": stamp, "action": "CANCEL_PENDING", "reason": "NO_OVERNIGHT_ORDERS"})
                pending = None
        previous_day = day
        # Execute only the prior confirmed bar's order. Current H/L/C are not
        # consulted when choosing its open fill.
        if pending is not None:
            action, signal_bar = pending
            if action == "BUY" and stamp < row.cutoff and position is None:
                fill = float(row.open)+cfg.slippage_points
                position = {"entry_time": stamp, "entry_price": fill, "entry_fee": fee(fill), "signal_bar": signal_bar}
                events.append({"time": stamp, "action": "BUY_FILLED", "reason": "NEXT_BAR_OPEN"})
            elif action == "SELL" and position is not None:
                if stamp >= row.cutoff:
                    raise ValueError("Missing exact square-off bar; exit would be late")
                close_position(stamp, float(row.open), "SBSM_EXIT", signal_bar)
            else:
                events.append({"time": stamp, "action": "ORDER_IGNORED", "reason": "CUTOFF_OR_FLAT_OR_ALREADY_LONG"})
            pending = None
        bar_close = stamp + pd.Timedelta(minutes=1)
        if stamp >= row.cutoff and position is not None:
            raise ValueError(f"Open position passed square-off on {day}; missing cutoff candle")
        # Signal state was calculated on ALL valid session bars; forced flat
        # does not reset trendState, cooldown, active cycle, ATR or WMA.
        if row.buy_signal:
            if bar_close < row.cutoff:
                if position is None:
                    pending = ("BUY", stamp)
                events.append({"time": bar_close, "action": "BUY_SIGNAL", "reason": "SBSM_BUY"})
            else:
                events.append({"time": bar_close, "action": "BUY_BLOCKED", "reason": "INTRADAY_CUTOFF"})
        elif row.sell_signal:
            events.append({"time": bar_close, "action": "SELL_SIGNAL", "reason": "SBSM_SELL"})
            if position is not None and bar_close < row.cutoff:
                pending = ("SELL", stamp)
        if bar_close == row.cutoff:
            pending = None
            if position is not None:
                reason = "SQUARE_OFF_" + cfg.square_off.replace(":", "") if row.cutoff.strftime("%H:%M") == cfg.square_off else "EARLY_SESSION_CLOSE"
                close_position(bar_close, float(row.close), reason)
        unrealized = 0.0 if position is None else (float(row.close)-position["entry_price"])*multiplier-position["entry_fee"]
        equity.append({"time": bar_close, "equity": cfg.initial_capital+realized+unrealized,
                       "realized_pnl": realized, "unrealized_pnl": unrealized, "long_open": position is not None})
    return pd.DataFrame(trades), pd.DataFrame(events, columns=["time", "action", "reason"]), pd.DataFrame(equity), position, pending


def summary(trades, equity, position, pending, cfg):
    net = trades.net_pnl.to_numpy() if len(trades) else np.array([])
    profit, loss = net[net > 0].sum(), -net[net < 0].sum()
    values = np.r_[cfg.initial_capital, equity.equity.to_numpy()]
    peaks = np.maximum.accumulate(values)
    return {"closed_trades": len(trades), "winning_trades": int((net > 0).sum()), "losing_trades": int((net < 0).sum()),
            "breakeven_trades": int((net == 0).sum()), "win_rate_pct": float((net > 0).mean()*100) if len(net) else None,
            "profit_factor": float(profit/loss) if loss else ("infinite" if profit else None),
            "net_realized_pnl": float(net.sum()), "average_trade_pnl": float(net.mean()) if len(net) else None,
            "net_price_points": float(trades.price_points.sum()) if len(trades) else 0.0,
            "gross_profit": float(profit), "gross_loss": float(loss),
            "bar_close_max_drawdown": float(np.max(peaks-values)),
            "bar_close_max_drawdown_pct": float(np.max((peaks-values)/peaks)*100),
            "last_marked_equity": float(values[-1]), "open_position_at_data_end": position,
            "pending_order_at_data_end": pending,
            "sizing": "fixed index units; not the live server's 12% stock allocation",
            "pnl_units": "index points when quantity=1 and point_value=1; not futures/options returns"}


def run(csv_path, output, cfg, overrides=None):
    data, quality = load_csv(Path(csv_path), cfg, overrides)
    signals = calculate_signals(data, cfg)
    trades, events, equity, position, pending = simulate(signals, cfg)
    metrics = summary(trades, equity, position, pending, cfg)
    metrics.update({"parameters": asdict(cfg), "input_quality": quality,
                    "first_bar": str(signals.index[0]), "last_bar": str(signals.index[-1]),
                    "signal_timeframe_minutes": 1, "input_sha256": hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
                    "pine_bar_parity_verified": False,
                    "comparison_note": "Signal formulas ported from project Pine; actual bar-by-bar TradingView comparison not performed. Provider history and warm-up can differ."})
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    trade_columns = ["entry_signal_bar", "entry_time", "entry_price", "exit_signal_bar", "exit_time", "exit_price", "exit_reason", "quantity", "point_value", "price_points", "gross_pnl", "commission", "net_pnl", "holding_minutes"]
    trades.reindex(columns=trade_columns).to_csv(output/"trades.csv", index=False)
    events.to_csv(output/"events.csv", index=False)
    equity.to_csv(output/"equity.csv", index=False)
    signals.to_csv(output/"bars_and_signals.csv", index_label="datetime_ist")
    (output/"summary.json").write_text(json.dumps(metrics, indent=2, default=str, allow_nan=False))
    if len(trades):
        monthly = trades.assign(month=pd.to_datetime(trades.exit_time).dt.strftime("%Y-%m")).groupby("month").agg(trades=("net_pnl", "size"), net_pnl=("net_pnl", "sum"), price_points=("price_points", "sum"))
        monthly.to_csv(output/"monthly.csv")
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for file in sorted(output.glob("*.csv")) + [output/"summary.json"]:
            z.write(file, file.name)
    return metrics, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--upload", action="store_true", help="Upload a CSV in Google Colab")
    parser.add_argument("--download", action="store_true", help="Download the result ZIP in Google Colab")
    parser.add_argument("--output", type=Path, default=Path("sbsm_results"))
    parser.add_argument("--sessions-json", type=Path, help='Date -> [["open", "close"], ...] overrides')
    for field in Config.__dataclass_fields__.values():
        default = field.default
        parser.add_argument("--"+field.name.replace("_", "-"), type=type(default), default=default)
    args = parser.parse_args()
    if args.upload:
        from google.colab import files
        uploaded = files.upload()
        names = [name for name in uploaded if name.lower().endswith(".csv")]
        if len(names) != 1:
            parser.error("Upload exactly one CSV")
        args.csv = Path(names[0])
    if args.csv is None:
        parser.error("Supply --csv path.csv, or use --upload in Google Colab")
    cfg = Config(**{name: getattr(args, name) for name in Config.__dataclass_fields__})
    overrides = json.loads(args.sessions_json.read_text()) if args.sessions_json else None
    metrics, archive = run(args.csv, args.output, cfg, overrides)
    print(json.dumps({k: v for k, v in metrics.items() if k not in ("open_position_at_data_end", "pending_order_at_data_end")}, indent=2, default=str))
    print(f"Results: {archive.resolve()}")
    if args.download:
        from google.colab import files
        files.download(str(archive))


if __name__ == "__main__":
    main()
