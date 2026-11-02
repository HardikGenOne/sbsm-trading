from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import httpx
import pandas_market_calendars as calendars

from .schemas import QuoteInput

IST = ZoneInfo("Asia/Kolkata")


class MarketCalendar:
    def __init__(self, closed_dates=()):
        self.calendar = calendars.get_calendar("NSE")
        self.closed_dates = set(closed_dates)

    @lru_cache(maxsize=400)
    def session(self, date_string):
        # Installed NSE calendar explicitly documents coverage only through 2026.
        # Fail closed rather than assume weekdays are trading days in a new year.
        if not "1996-01-01" <= date_string <= "2026-12-31":
            raise RuntimeError("NSE calendar outside reviewed 1996-2026 coverage")
        if date_string in self.closed_dates:
            return None
        schedule = self.calendar.schedule(start_date=date_string, end_date=date_string)
        if schedule.empty:
            return None
        row = schedule.iloc[0]
        return int(row.market_open.timestamp() * 1000), int(row.market_close.timestamp() * 1000)

    def is_open(self, now: int):
        date = datetime.fromtimestamp(now / 1000, IST).date().isoformat()
        session = self.session(date)
        return bool(session and session[0] <= now < session[1])

    def next_session_open(self, now: int):
        date = datetime.fromtimestamp(now / 1000, IST).date()
        for offset in range(1, 31):
            session = self.session((date + timedelta(days=offset)).isoformat())
            if session:
                return session[0]
        raise RuntimeError("No settlement session found; calendar needs review")


class KiteQuotes:
    """Read-only HTTP client. Deliberately contains no order-placement methods."""

    def __init__(self, settings):
        self.api_key = settings.kite_api_key.get_secret_value()
        self.access_token = settings.kite_access_token.get_secret_value()
        self.client = httpx.Client(base_url="https://api.kite.trade", timeout=5)
        self.tick_sizes = {}
        self.instrument_date = None

    @property
    def configured(self):
        return bool(self.api_key and self.access_token)

    def fetch(self, symbols: list[str]) -> list[QuoteInput]:
        if not self.configured or not symbols:
            return []
        headers = {"X-Kite-Version": "3", "Authorization": f"token {self.api_key}:{self.access_token}"}
        # Instrument metadata, including actual stock tick sizes, is mandatory.
        today = datetime.now(IST).date()
        if not self.tick_sizes or self.instrument_date != today:
            import csv
            import io
            response = self.client.get("/instruments/NSE", headers=headers)
            response.raise_for_status()
            tick_sizes = {}
            for row in csv.DictReader(io.StringIO(response.text)):
                if row.get("segment") == "NSE" and row.get("instrument_type") == "EQ":
                    tick_sizes[row["tradingsymbol"]] = row["tick_size"]
            self.tick_sizes = tick_sizes
            self.instrument_date = today
        response = self.client.get("/quote", params=[("i", "NSE:" + s) for s in symbols], headers=headers)
        response.raise_for_status()
        result = []
        for symbol in symbols:
            value = response.json().get("data", {}).get("NSE:" + symbol)
            if not value or symbol not in self.tick_sizes:
                continue
            buys = [x for x in value.get("depth", {}).get("buy", []) if x["price"] > 0 and x["quantity"] > 0]
            sells = [x for x in value.get("depth", {}).get("sell", []) if x["price"] > 0 and x["quantity"] > 0]
            if not buys or not sells:
                continue
            stamp = datetime.fromisoformat(value["timestamp"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=IST)
            bid, ask = max(buys, key=lambda x: x["price"]), min(sells, key=lambda x: x["price"])
            result.append(QuoteInput(symbol=symbol, asof_ms=int(stamp.timestamp() * 1000), bid=str(bid["price"]), ask=str(ask["price"]), bid_quantity=bid["quantity"], ask_quantity=ask["quantity"], last_price=str(value["last_price"]), tick_size=self.tick_sizes[symbol]))
        return result

    def close(self):
        self.client.close()
