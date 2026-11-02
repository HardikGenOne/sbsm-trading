from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = tuple("POLYCAB ADANIENT APARINDS APLAPOLLO BAJAJ_AUTO BEL BHEL BSE CANBK CONCOR EICHERMOT FORTIS HINDCOPPER ICICIBANK LAURUSLABS M&MFIN MCX NTPC SBIN ZYDUSLIFE".split())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SBSM_", env_file=ROOT / ".env", extra="ignore")
    admin_token: SecretStr
    webhook_token: SecretStr
    database_url: str = f"sqlite:///{ROOT / 'data/paper.db'}"
    feed_mode: Literal["kite", "manual"] = "kite"
    kite_api_key: SecretStr = SecretStr("")
    kite_access_token: SecretStr = SecretStr("")
    initial_capital_paise: int = Field(50_000_000, gt=0)
    allocation_bps: int = Field(1200, gt=0, le=10000)
    approved_config: str = "lb=4;gap=0.16;noise=15;atr=15;factor=1.8;wma=20"
    entry_ttl_seconds: int = Field(60, ge=1, le=3600)
    signal_max_age_seconds: int = Field(120, ge=1)
    execution_latency_ms: int = Field(500, ge=0)
    quote_max_age_seconds: int = Field(10, ge=1)
    slippage_bps: int = Field(5, ge=0, le=1000)
    price_protection_bps: int = Field(100, ge=1, le=5000)
    depth_participation_bps: int = Field(1000, ge=1, le=10000)
    buy_cost_bps: int = Field(15, ge=0, le=1000)
    sell_cost_bps: int = Field(15, ge=0, le=1000)
    sell_fixed_cost_paise: int = Field(2000, ge=0)
    settlement_mode: Literal["next_session", "immediate"] = "next_session"
    poll_seconds: float = Field(2, ge=0.1)
    closed_dates: list[str] = []

    @model_validator(mode="after")
    def secrets_are_usable(self):
        a, b = self.admin_token.get_secret_value(), self.webhook_token.get_secret_value()
        if min(len(a), len(b)) < 32 or a == b or a.startswith("replace-") or b.startswith("replace-"):
            raise ValueError("Set distinct random admin/webhook tokens, each at least 32 characters")
        return self
