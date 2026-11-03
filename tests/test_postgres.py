"""Opt-in integration checks in disposable schemas, never the cloud ledger."""
import os
from pathlib import Path
import uuid

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from sbsm.config import Settings
from sbsm.database import Database
from sbsm.engine import PaperEngine
from conftest import Calendar, Clock
import test_engine as scenarios

pytestmark = pytest.mark.skipif(os.environ.get("SBSM_TEST_POSTGRES") != "1", reason="Explicit cloud SQL integration check not requested")


@pytest.fixture
def postgres_system():
    values = dotenv_values(Path(__file__).resolve().parent.parent / ".env.render")
    url = make_url(values["SBSM_DATABASE_URL"])
    assert url.get_backend_name() == "postgresql"
    schema = "sbsm_test_" + uuid.uuid4().hex
    management = create_engine(url)
    with management.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    clock, calendar = Clock(), Calendar()
    settings = Settings(_env_file=None, admin_token="a" * 40, webhook_token="b" * 40,
                        database_url=isolated.render_as_string(hide_password=False), feed_mode="manual",
                        execution_latency_ms=0, settlement_mode="immediate", slippage_bps=0,
                        price_protection_bps=100, depth_participation_bps=10000)
    db = Database(settings.database_url)
    try:
        db.initialize(settings.initial_capital_paise, clock())
        yield PaperEngine(db, settings, calendar, clock), clock, calendar
    finally:
        db.engine.dispose()
        with management.begin() as connection:
            # Only the random test-owned schema is removed.
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        management.dispose()


@pytest.mark.parametrize("scenario", [
    scenarios.test_fifo_eight_reservations_and_ninth_skipped,
    scenarios.test_concurrent_ingestion_and_workers_cannot_overspend,
    scenarios.test_duplicate_and_conflicting_delivery,
    scenarios.test_roundtrip_net_pnl_and_dynamic_allocation,
    scenarios.test_restart_recovers_pending_and_never_replays_skips,
])
def test_same_accounting_scenarios_on_postgres(postgres_system, scenario):
    scenario(postgres_system)
