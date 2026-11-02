"""Deterministic paper-only walkthrough in a disposable database, not the real ledger."""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory

from sqlalchemy import select

from sbsm.config import Settings, SYMBOLS
from sbsm.database import Database
from sbsm.engine import PaperEngine
from sbsm.models import Event
from sbsm.schemas import QuoteInput, Signal


class DemoTime:
    value = int(datetime.fromisoformat("2026-11-02T11:15:01+05:30").timestamp() * 1000)

    def __call__(self):
        return self.value


class DemoSession:
    def is_open(self, _):
        return True


def make_signal(clock, config, symbol, action="ENTER_LONG", cycle=None):
    close = clock() - 1000
    instance = f"SBSM:1.0.0:NSE:{symbol}:120:{config}"
    return Signal(schema_version=1, strategy_id="SBSM", strategy_version="1.0.0", strategy_instance_id=instance, config_key=config, event_id=f"{instance}:{close}:{action}", trade_cycle_id=cycle or f"{instance}:{close}", exchange="NSE", symbol=symbol, ticker_id="NSE:" + symbol, timeframe="120", action=action, reason="SBSM_BUY" if action == "ENTER_LONG" else "SBSM_SELL", signal_bar_open_ms=close - 7_200_000, signal_bar_close_ms=close, emitted_at_ms=close + 500, reference_price=Decimal("1000"))


def main():
    root = Path(__file__).resolve().parent.parent
    (root / "work").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=root / "work", prefix="demo-") as folder:
        settings = Settings(_env_file=None, admin_token=secrets.token_urlsafe(40), webhook_token=secrets.token_urlsafe(40), database_url=f"sqlite:///{folder}/demo.db", feed_mode="manual", settlement_mode="immediate")
        clock = DemoTime()
        db = Database(settings.database_url)
        db.initialize(settings.initial_capital_paise, clock())
        engine = PaperEngine(db, settings, DemoSession(), clock)
        entries = []
        for symbol in SYMBOLS[:9]:
            entry = make_signal(clock, settings.approved_config, symbol)
            entries.append(entry)
            engine.ingest(entry)
        engine.step()
        print("Nine FIFO entries: eight accepted; ninth skipped for insufficient funds.")
        clock.value += 1000
        for symbol in SYMBOLS[:8]:
            engine.quote(QuoteInput(symbol=symbol, asof_ms=clock(), bid=1000, ask=1000, bid_quantity=10000, ask_quantity=10000, last_price=1000), "manual")
        engine.step()
        print("Filled eight paper positions using separate simulated bid/ask snapshots.")
        clock.value += 1000
        engine.ingest(make_signal(clock, settings.approved_config, SYMBOLS[0], "EXIT_LONG", entries[0].trade_cycle_id))
        engine.step()
        clock.value += 1000
        engine.quote(QuoteInput(symbol=SYMBOLS[0], asof_ms=clock(), bid=1100, ask=1101, bid_quantity=10000, ask_quantity=10000, last_price=1100), "manual")
        engine.step()
        p = engine.portfolio()
        with db.read() as s:
            skipped = s.scalars(select(Event).where(Event.status == "SKIPPED")).all()
            reasons = [{"symbol": e.symbol, "reason": e.reason} for e in skipped]
        print(json.dumps({"demo_only": True, "settlement_assumption": "immediate for walkthrough only", "fund_pool_rupees": p["pool_paise"] / 100, "next_trade_budget_rupees": p["next_allocation_paise"] / 100, "realized_net_rupees": p["realized_net_paise"] / 100, "remaining_positions": len(p["positions"]), "skipped_signals": reasons}, indent=2))
        print("Your real paper account was not changed.")
        db.engine.dispose()


if __name__ == "__main__":
    main()
