import sqlite3
import stat

import pytest

from sbsm.backup import backup_sqlite
from conftest import enter


def test_online_backup_includes_committed_wal_data_and_preserves_source(system, tmp_path):
    engine, clock, _ = system
    enter(engine, clock)
    before = engine.portfolio()
    target = backup_sqlite(engine.settings.database_url, tmp_path / "backup.sqlite3")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
        assert connection.execute("SELECT cash FROM accounts WHERE id=1").fetchone()[0] == before["cash_paise"]
    assert engine.portfolio() == before
    with pytest.raises(ValueError):
        backup_sqlite(engine.settings.database_url, target)


def test_backup_never_creates_missing_source(tmp_path):
    source = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        backup_sqlite(f"sqlite:///{source}")
    assert not source.exists()
