"""Read-only Dhan equity market-depth snapshots for SBSM paper fills."""
import csv
import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from zoneinfo import ZoneInfo

import httpx

from .config import SYMBOLS
from .schemas import QuoteInput
from .quote_cache import locked_snapshot

IST = ZoneInfo("Asia/Kolkata")


class DhanQuotes:
    def __init__(self, settings, client=None):
        self.client_id = settings.dhan_client_id.get_secret_value()
        self.access_token = settings.dhan_access_token.get_secret_value()
        self.client = client if client is not None else httpx.Client(base_url="https://api.dhan.co", timeout=5)
        self.instruments = {}
        self.instrument_date = None
        # Rotation gets a new cache; never reuse quotes from another credential.
        identity = hashlib.sha256((self.client_id + ":" + self.access_token).encode()).hexdigest()
        self.cache_path = settings.market_cache_dir / ("dhan-" + identity + ".json")

    @property
    def configured(self):
        return bool(self.client_id and self.access_token)

    def refresh_instruments(self):
        today = datetime.now(IST).date()
        if self.instrument_date == today:
            return
        # The public CSV redirects to a CDN. Broker credentials are never sent here.
        response = self.client.get("/v2/instrument/NSE_EQ", follow_redirects=True)
        response.raise_for_status()
        instruments = {}
        for row in csv.DictReader(response.text.splitlines()):
            exchange_symbol = row.get("UNDERLYING_SYMBOL")
            symbol = "BAJAJ_AUTO" if exchange_symbol == "BAJAJ-AUTO" else exchange_symbol
            if symbol not in SYMBOLS or (row.get("EXCH_ID"), row.get("SEGMENT"), row.get("INSTRUMENT"), row.get("SERIES")) != ("NSE", "E", "EQUITY", "EQ"):
                continue
            security_id = int(row["SECURITY_ID"])
            # Dhan's instrument file expresses the tick in paise.
            tick = Decimal(row["TICK_SIZE"]) / 100
            if symbol in instruments or security_id <= 0 or not tick.is_finite() or tick < Decimal("0.01"):
                raise ValueError("Invalid Dhan equity instrument metadata")
            instruments[symbol] = (security_id, tick)
        if not instruments:
            raise ValueError("Dhan returned no approved equity instruments")
        self.instruments, self.instrument_date = instruments, today

    def snapshot(self):
        with locked_snapshot(self.cache_path) as state:
            now = time.time()
            age = now - state.get("fetched_at", 0)
            if state.get("body") and 0 <= age < 2:
                return json.loads(state["body"], parse_float=Decimal)
            retry_at = state.get("retry_at", 0)
            if now < retry_at:
                response = httpx.Response(state.get("error_status", 503),
                    headers={"Retry-After": str(max(1, math.ceil(retry_at - now)))},
                    request=httpx.Request("POST", "https://api.dhan.co/v2/marketfeed/quote"))
                response.raise_for_status()
            today = datetime.now(IST).date().isoformat()
            if state.get("instrument_date") == today:
                self.instruments = {symbol: (entry[0], Decimal(entry[1]))
                                    for symbol, entry in state["instruments"].items()}
                self.instrument_date = datetime.now(IST).date()
            try:
                self.refresh_instruments()
                state["instrument_date"] = today
                state["instruments"] = {symbol: [entry[0], str(entry[1])]
                                        for symbol, entry in self.instruments.items()}
                # Fetch the whole 20-stock universe once; tests and the worker
                # reuse this response instead of spending separate rate slots.
                time.sleep(max(0, state.get("last_request", 0) + 1.25 - time.time()))
                state["last_request"] = time.time()
                response = self.client.post(
                    "/v2/marketfeed/quote",
                    json={"NSE_EQ": [entry[0] for entry in self.instruments.values()]},
                    headers={"client-id": self.client_id, "access-token": self.access_token},
                )
                response.raise_for_status()
                payload = response.json(parse_float=Decimal)
                if payload.get("status") != "success":
                    raise ValueError("Dhan rejected the market quote request")
            except (httpx.HTTPError, ValueError) as exc:
                failures = min(state.get("failures", 0) + 1, 5)
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 503
                delay = min(30, 2 ** failures)
                if status in (401, 403):
                    delay = 10
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        delay = max(delay, min(60, float(exc.response.headers.get("Retry-After", 0))))
                    except ValueError:
                        pass
                state.update(failures=failures, error_status=status, retry_at=time.time() + delay)
                raise
            state.update(body=response.text, fetched_at=time.time(), failures=0, retry_at=0)
            return payload

    def fetch(self, symbols: list[str]) -> list[QuoteInput]:
        if not self.configured or not symbols:
            return []
        payload = self.snapshot()
        # Also restore metadata on cache hits made by a different process.
        with locked_snapshot(self.cache_path) as state:
            self.instruments = {symbol: (entry[0], Decimal(entry[1]))
                                for symbol, entry in state.get("instruments", {}).items()}
        requested = {symbol: self.instruments[symbol] for symbol in symbols if symbol in self.instruments}
        snapshots = payload.get("data", {}).get("NSE_EQ", {})
        result = []
        for symbol, (security_id, tick) in requested.items():
            value = snapshots.get(str(security_id))
            if not value:
                continue
            try:
                depth = value["depth"]
                bid = max((row for row in depth["buy"] if row["price"] > 0 and row["quantity"] > 0), key=lambda row: row["price"])
                ask = min((row for row in depth["sell"] if row["price"] > 0 and row["quantity"] > 0), key=lambda row: row["price"])
                # Last trade time is a conservative freshness proxy. Never substitute fetch time.
                stamp = datetime.strptime(value["last_trade_time"], "%d/%m/%Y %H:%M:%S").replace(tzinfo=IST)
                if stamp.date() != datetime.now(IST).date():
                    continue
                quote = QuoteInput(
                    symbol=symbol, asof_ms=int(stamp.timestamp() * 1000), bid=bid["price"], ask=ask["price"],
                    bid_quantity=bid["quantity"], ask_quantity=ask["quantity"],
                    last_price=value["last_price"], tick_size=tick,
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            result.append(quote)
        return result

    def close(self):
        self.client.close()
