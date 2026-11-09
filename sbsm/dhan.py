"""Read-only Dhan equity market-depth snapshots for SBSM paper fills."""
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from time import monotonic
from zoneinfo import ZoneInfo

import httpx

from .config import SYMBOLS
from .schemas import QuoteInput

IST = ZoneInfo("Asia/Kolkata")


class DhanQuotes:
    def __init__(self, settings, client=None):
        self.client_id = settings.dhan_client_id.get_secret_value()
        self.access_token = settings.dhan_access_token.get_secret_value()
        self.client = client if client is not None else httpx.Client(base_url="https://api.dhan.co", timeout=5)
        self.instruments = {}
        self.instrument_date = None
        self.next_request = 0.0

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

    def fetch(self, symbols: list[str]) -> list[QuoteInput]:
        if not self.configured or not symbols or monotonic() < self.next_request:
            return []
        self.refresh_instruments()
        requested = {symbol: self.instruments[symbol] for symbol in symbols if symbol in self.instruments}
        if not requested:
            return []
        self.next_request = monotonic() + 1
        response = self.client.post(
            "/v2/marketfeed/quote",
            json={"NSE_EQ": [security_id for security_id, _ in requested.values()]},
            headers={"client-id": self.client_id, "access-token": self.access_token},
        )
        response.raise_for_status()
        payload = response.json(parse_float=Decimal)
        if payload.get("status") != "success":
            raise ValueError("Dhan rejected the market quote request")
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
