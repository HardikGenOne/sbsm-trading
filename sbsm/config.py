from pathlib import Path
from typing import Literal

import re
import ipaddress
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = tuple("POLYCAB ADANIENT APARINDS APLAPOLLO BAJAJ_AUTO BEL BHEL BSE CANBK CONCOR EICHERMOT FORTIS HINDCOPPER ICICIBANK LAURUSLABS M&MFIN MCX NTPC SBIN ZYDUSLIFE".split())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SBSM_", env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True)
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
    session_seconds: int = Field(3600, ge=60, le=86400)
    session_cookie_secure: bool = False
    environment: Literal["local", "production"] = "local"
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    webhook_allowed_ips: list[str] = []
    public_base_url: str = ""
    forwarded_allow_ips: str = "127.0.0.1"
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: SecretStr = SecretStr("")
    telegram_allow_groups: bool = False
    telegram_max_attempts: int = Field(8, ge=1, le=30)
    notification_poll_seconds: float = Field(1.5, ge=1)

    @model_validator(mode="after")
    def secrets_are_usable(self):
        a, b = self.admin_token.get_secret_value(), self.webhook_token.get_secret_value()
        if min(len(a), len(b)) < 32 or a == b or a.startswith("replace-") or b.startswith("replace-"):
            raise ValueError("Set distinct random admin/webhook tokens, each at least 32 characters")
        if self.telegram_enabled:
            token = self.telegram_bot_token.get_secret_value()
            chat = self.telegram_chat_id.get_secret_value()
            if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token) or not re.fullmatch(r"-?[1-9][0-9]*", chat):
                raise ValueError("Configure a valid Telegram bot token and numeric chat ID locally")
            if chat.startswith("-") and not self.telegram_allow_groups:
                raise ValueError("Group notifications require explicit telegram_allow_groups=true")
        if self.environment == "production":
            parsed = urlparse(self.public_base_url)
            if not self.session_cookie_secure or parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("Production requires HTTPS public_base_url and Secure session cookies")
            if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
                raise ValueError("public_base_url must be an HTTPS origin without credentials, query, or path")
            if any("*" in host for host in self.allowed_hosts) or parsed.hostname not in self.allowed_hosts:
                raise ValueError("Production allowed_hosts must include the public domain without wildcards")
            if "127.0.0.1" not in self.allowed_hosts:
                raise ValueError("Production allowed_hosts must include 127.0.0.1 for internal health checks")
            if not self.webhook_allowed_ips:
                raise ValueError("Production requires an explicit TradingView IP allowlist")
        for address in self.webhook_allowed_ips:
            try:
                ipaddress.ip_address(address)
            except ValueError:
                raise ValueError("Webhook allowlist must contain IP addresses") from None
        return self
