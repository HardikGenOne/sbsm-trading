"""Download NIFTY 50 spot 1-minute OHLCV from Dhan, without placing orders.

Run from the project root:
  .venv/bin/python scripts/download_dhan_nifty.py --sbsm-cloud --start 2024-01-01
Or set DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID in your local environment and omit
--sbsm-cloud. Tokens are never written into the export or printed.
"""
import argparse
import csv
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

# Allow running the script directly from a project checkout.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx

IST = ZoneInfo("Asia/Kolkata")
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
SOURCE = "https://dhanhq.co/docs/v2/historical-data/"


class DownloadError(Exception):
    pass


def parse_date(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp.astimezone(IST)
    except ValueError:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD or an ISO date/time") from None


def credentials(cloud):
    if cloud:
        from dotenv import dotenv_values
        from sbsm.config import Settings
        from sbsm.database import Database
        from sbsm.dhan_auth import effective_settings
        values = dotenv_values(ROOT / ".env.render")
        os.environ.update({key: value for key, value in values.items() if value is not None})
        settings = Settings(_env_file=None)
        db = Database(settings.database_url)
        try:
            current = effective_settings(settings, db)
            token = current.dhan_access_token.get_secret_value()
            client_id = current.dhan_client_id.get_secret_value()
        finally:
            db.engine.dispose()
    else:
        token = os.environ.get("DHAN_ACCESS_TOKEN") or os.environ.get("SBSM_DHAN_ACCESS_TOKEN", "")
        client_id = os.environ.get("DHAN_CLIENT_ID") or os.environ.get("SBSM_DHAN_CLIENT_ID", "")
    if not token or not client_id:
        raise DownloadError("No valid Dhan token configured. Set credentials locally; never paste them into chat.")
    return token, client_id


def decode_bars(payload):
    if not isinstance(payload, dict) or any(not isinstance(payload.get(key), list) for key in FIELDS):
        raise DownloadError("Unexpected candle response; no data substituted")
    if len({len(payload[key]) for key in FIELDS}) != 1:
        raise DownloadError("Mismatched candle arrays")
    result = []
    for values in zip(*(payload[key] for key in FIELDS)):
        stamp, *prices, volume = values
        if type(stamp) not in (int, float) or int(stamp) != stamp or int(stamp) % 60:
            raise DownloadError("Non-minute-aligned timestamp")
        try:
            o, h, l, c = [Decimal(str(value)) for value in prices]
            vol = Decimal(str(volume))
        except (InvalidOperation, ValueError):
            raise DownloadError("Invalid candle numeric value") from None
        if any(not value.is_finite() or value <= 0 for value in (o, h, l, c)):
            raise DownloadError("Non-positive or non-finite index price")
        if not vol.is_finite() or vol < 0 or vol != int(vol):
            raise DownloadError("Invalid candle volume")
        result.append((int(stamp), *(str(value) for value in (o, h, l, c)), int(vol)))
    return result


def fetch(client, request, cloud):
    for attempt in range(5):
        token, client_id = credentials(cloud)
        try:
            response = client.post("/v2/charts/intraday", headers={"access-token": token, "client-id": client_id}, json=request)
        except httpx.HTTPError:
            if attempt == 4:
                raise DownloadError("Historical-data request timed out after bounded retries") from None
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 4:
                raise DownloadError(f"Broker HTTP {response.status_code}; saved completed chunks can be resumed")
            time.sleep(min(30, 2 ** (attempt + 1)))
            continue
        try:
            payload = response.json()
        except ValueError:
            raise DownloadError("Broker returned non-JSON historical data") from None
        if response.status_code != 200:
            # Broker message bodies can contain credentials; expose code only.
            code = str(payload.get("errorCode", "")) if isinstance(payload, dict) else ""
            safe_code = code if code.startswith("DH-") and code[3:].isdigit() else "unavailable"
            raise DownloadError(f"Broker HTTP {response.status_code}, code {safe_code}")
        decode_bars(payload)
        return payload
    raise DownloadError("Historical data unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, default=parse_date("2024-01-01"))
    parser.add_argument("--end", type=parse_date, help="Exclusive end in IST; defaults to now")
    parser.add_argument("--sbsm-cloud", action="store_true", help="Read current encrypted SBSM token; never renew it")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "work/nifty-download-cache")
    args = parser.parse_args()
    fetched_at = datetime.now(IST)
    end = min(args.end or fetched_at, fetched_at).replace(second=0, microsecond=0)
    start = args.start
    if end <= start:
        raise DownloadError("End must be after start")
    output = args.output or ROOT / f"work/exports/NIFTY50_SPOT_1min_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    bars, chunks, duplicates = {}, [], 0
    current = start
    with httpx.Client(base_url="https://api.dhan.co", timeout=45, follow_redirects=False) as client:
        while current < end:
            stop = min(current + timedelta(days=30), end)
            request = {"securityId": "13", "exchangeSegment": "IDX_I", "instrument": "INDEX", "interval": "1", "oi": False,
                       "fromDate": current.strftime("%Y-%m-%d %H:%M:%S"), "toDate": stop.strftime("%Y-%m-%d %H:%M:%S")}
            digest = sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
            cached = args.cache_dir / (digest + ".json")
            if cached.exists():
                payload = json.loads(cached.read_text())
            else:
                payload = fetch(client, request, args.sbsm_cloud)
                cached.write_text(json.dumps(payload, separators=(",", ":")))
                time.sleep(1.3)  # Gentle, sequential requests; no quote-feed changes.
            decoded = decode_bars(payload)
            accepted = 0
            for bar in decoded:
                stamp = bar[0]
                if not int(start.timestamp()) <= stamp or stamp + 60 > int(end.timestamp()):
                    continue
                if stamp in bars:
                    if bars[stamp] != bar:
                        raise DownloadError("Conflicting duplicate candle; export halted")
                    duplicates += 1
                else:
                    bars[stamp] = bar
                accepted += 1
            chunks.append({"from_ist": current.isoformat(), "to_ist": stop.isoformat(), "returned_rows": len(decoded), "accepted_rows": accepted})
            print(f"{current:%Y-%m-%d} to {stop:%Y-%m-%d}: {len(decoded):,} candles; {len(bars):,} unique total", flush=True)
            current = stop
    if not bars:
        raise DownloadError("No candles returned; CSV not created")
    ordered = sorted(bars.values())
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("datetime_ist", "timestamp_unix", "open", "high", "low", "close", "volume"))
        for stamp, *values in ordered:
            writer.writerow((datetime.fromtimestamp(stamp, IST).isoformat(), stamp, *values))
    temporary.replace(output)
    days = {}
    gaps = []
    for bar in ordered:
        dt = datetime.fromtimestamp(bar[0], IST)
        days[dt.date().isoformat()] = days.get(dt.date().isoformat(), 0) + 1
    for before, after in zip(ordered, ordered[1:]):
        left, right = datetime.fromtimestamp(before[0], IST), datetime.fromtimestamp(after[0], IST)
        if left.date() == right.date() and after[0] - before[0] > 60:
            gaps.append({"after_ist": left.isoformat(), "before_ist": right.isoformat(), "missing_minutes": (after[0]-before[0])//60-1})
    anomalies = []
    for bar in ordered:
        stamp, o, h, l, c, _ = bar
        o, h, l, c = map(Decimal, (o, h, l, c))
        if not l <= min(o, c) <= max(o, c) <= h:
            anomalies.append({"datetime_ist": datetime.fromtimestamp(stamp, IST).isoformat(),
                              "open": str(o), "high": str(h), "low": str(l), "close": str(c)})
    metadata = {"source": "Dhan API /v2/charts/intraday", "documentation": SOURCE, "symbol": "NIFTY 50 spot index", "security_id": "13",
                "exchange_segment": "IDX_I", "instrument": "INDEX", "interval_minutes": 1,
                "timezone": "Asia/Kolkata", "timestamp_meaning": "Candle opening timestamp; only completed minutes included",
                "requested_from_ist": start.isoformat(), "requested_to_exclusive_ist": end.isoformat(), "downloaded_at_ist": fetched_at.isoformat(),
                "first_candle_ist": datetime.fromtimestamp(ordered[0][0], IST).isoformat(), "last_candle_ist": datetime.fromtimestamp(ordered[-1][0], IST).isoformat(),
                "rows": len(ordered), "trading_dates_returned": len(days), "duplicate_rows_removed": duplicates,
                "ohlc_checks": "passed" if not anomalies else "source OHLC anomalies retained without modification", "ohlc_anomaly_count": len(anomalies), "ohlc_anomalies": anomalies, "same_day_gap_count": len(gaps), "same_day_gaps": gaps,
                "bars_per_date": days, "chunks": chunks,
                "csv_sha256": sha256(output.read_bytes()).hexdigest(),
                "notes": ["Spot index, not futures, options or an ETF.", "Volume is returned as supplied by Dhan; index volume may be zero and is not cash-market turnover.",
                          "No holiday/weekend candles are invented or gaps forward-filled.", "Same-day gaps may reflect scheduled breaks or source omissions; this report does not assume continuous trading during a special-session break.", "Coverage reflects candles returned by Dhan, not an independently certified exchange-complete dataset."]}
    output.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2))
    print(json.dumps({"csv": str(output), "rows": len(ordered), "first": metadata['first_candle_ist'], "last": metadata['last_candle_ist'], "same_day_gaps": len(gaps), "ohlc_anomalies": len(anomalies)}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not emit raw requests, headers, SQL URLs or broker error bodies.
        print(str(error) if isinstance(error, DownloadError) else f"Download failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1)
