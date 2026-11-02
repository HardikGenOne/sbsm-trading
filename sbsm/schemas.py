import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .config import SYMBOLS


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1]
    strategy_id: Literal["SBSM"]
    strategy_version: Literal["1.0.0"]
    strategy_instance_id: str = Field(max_length=1024)
    config_key: str = Field(max_length=512)
    event_id: str = Field(max_length=1200)
    trade_cycle_id: str = Field(max_length=1200)
    exchange: Literal["NSE"]
    symbol: str
    ticker_id: str
    timeframe: Literal["120"]
    action: Literal["ENTER_LONG", "EXIT_LONG"]
    reason: Literal["SBSM_BUY", "SBSM_SELL"]
    signal_bar_open_ms: int = Field(gt=0, strict=True)
    signal_bar_close_ms: int = Field(gt=0, strict=True)
    emitted_at_ms: int = Field(gt=0, strict=True)
    reference_price: Decimal = Field(gt=0, max_digits=16, decimal_places=4)

    @field_validator("reference_price", mode="before")
    @classmethod
    def numeric_price(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("reference_price must be a JSON number")
        return value

    @field_serializer("reference_price", when_used="json")
    def json_price(self, value):
        return float(value)

    @model_validator(mode="after")
    def cross_fields(self):
        if self.symbol not in SYMBOLS or self.ticker_id != f"NSE:{self.symbol}":
            raise ValueError("Unapproved stock or ticker mismatch")
        instance = f"SBSM:1.0.0:{self.ticker_id}:120:{self.config_key}"
        if self.strategy_instance_id != instance:
            raise ValueError("strategy_instance_id mismatch")
        if self.event_id != f"{instance}:{self.signal_bar_close_ms}:{self.action}":
            raise ValueError("event_id mismatch")
        prefix = instance + ":"
        suffix = self.trade_cycle_id.removeprefix(prefix)
        if not self.trade_cycle_id.startswith(prefix) or not re.fullmatch(r"[1-9][0-9]*", suffix):
            raise ValueError("trade_cycle_id mismatch")
        cycle_ms = int(suffix)
        if self.action == "ENTER_LONG" and cycle_ms != self.signal_bar_close_ms:
            raise ValueError("Entry cycle must match entry bar close")
        if self.action == "EXIT_LONG" and cycle_ms >= self.signal_bar_close_ms:
            raise ValueError("Exit must follow its entry cycle")
        if not self.signal_bar_open_ms < self.signal_bar_close_ms <= self.emitted_at_ms:
            raise ValueError("Invalid timestamp ordering")
        if self.reason != ("SBSM_BUY" if self.action == "ENTER_LONG" else "SBSM_SELL"):
            raise ValueError("Action/reason mismatch")
        return self


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    symbol: str
    asof_ms: int = Field(gt=0, strict=True)
    bid: Decimal = Field(ge=Decimal("0.01"), max_digits=16, decimal_places=2)
    ask: Decimal = Field(ge=Decimal("0.01"), max_digits=16, decimal_places=2)
    bid_quantity: int = Field(ge=0, strict=True)
    ask_quantity: int = Field(ge=0, strict=True)
    last_price: Decimal = Field(ge=Decimal("0.01"), max_digits=16, decimal_places=2)
    tick_size: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0.01"), decimal_places=2)

    @model_validator(mode="after")
    def valid_market(self):
        if self.symbol not in SYMBOLS or self.bid > self.ask:
            raise ValueError("Invalid stock or crossed quote")
        return self
