from dataclasses import dataclass
from decimal import Decimal

import pytest

from sbsm.config import Settings
from sbsm.database import Database
from sbsm.engine import PaperEngine
from sbsm.schemas import QuoteInput, Signal


@dataclass
class Clock:
    value: int = 1793598301000

    def __call__(self):
        return self.value

    def advance(self, milliseconds=1000):
        self.value += milliseconds


class Calendar:
    opened = True

    def is_open(self, now):
        return self.opened

    def next_session_open(self, now):
        return now + 86_400_000


@pytest.fixture
def system(tmp_path):
    clock, calendar = Clock(), Calendar()
    settings = Settings(_env_file=None, admin_token="a" * 40, webhook_token="b" * 40, database_url=f"sqlite:///{tmp_path / 'test.db'}", feed_mode="manual", execution_latency_ms=0, settlement_mode="immediate", slippage_bps=0, price_protection_bps=100, depth_participation_bps=10000)
    db = Database(settings.database_url)
    db.initialize(settings.initial_capital_paise, clock())
    engine = PaperEngine(db, settings, calendar, clock)
    yield engine, clock, calendar
    db.engine.dispose()


def make_signal(engine, symbol="ICICIBANK", action="ENTER_LONG", cycle=None, price="1000"):
    close = engine.clock() - 1000
    config = engine.settings.approved_config
    instance = f"SBSM:1.0.0:NSE:{symbol}:120:{config}"
    return Signal(schema_version=1, strategy_id="SBSM", strategy_version="1.0.0", strategy_instance_id=instance, config_key=config, event_id=f"{instance}:{close}:{action}", trade_cycle_id=cycle or f"{instance}:{close}", exchange="NSE", symbol=symbol, ticker_id="NSE:" + symbol, timeframe="120", action=action, reason="SBSM_BUY" if action == "ENTER_LONG" else "SBSM_SELL", signal_bar_open_ms=close-7_200_000, signal_bar_close_ms=close, emitted_at_ms=close+500, reference_price=Decimal(price))


def tick(engine, symbol="ICICIBANK", bid="1000", ask="1000", size=10000):
    return engine.quote(QuoteInput(symbol=symbol, asof_ms=engine.clock(), bid=Decimal(bid), ask=Decimal(ask), bid_quantity=size, ask_quantity=size, last_price=Decimal(bid)), "manual")


def enter(engine, clock, symbol="ICICIBANK"):
    signal = make_signal(engine, symbol)
    engine.ingest(signal)
    engine.step()
    clock.advance()
    tick(engine, symbol)
    engine.step()
    return signal
