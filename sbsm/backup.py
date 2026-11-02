"""Consistent SQLite online backup; never copies a live WAL database as a raw file."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid

from sqlalchemy.engine import make_url

from .config import Settings


def backup_sqlite(database_url, target=None):
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("This backup tool requires a persistent SQLite database")
    source = Path(url.database).resolve()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    target = Path(target) if target else source.parent / "backups" / f"paper-{timestamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    if target.exists():
        raise ValueError("Backup destination already exists; refusing to overwrite it")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / (".backup-" + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(temporary) as destination:
            original.backup(destination, pages=256, sleep=0.05)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity verification failed")
        # Atomic no-clobber publication on the same filesystem.
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        target = backup_sqlite(Settings().database_url, args.output)
    except (ValueError, RuntimeError):
        raise SystemExit("Backup failed. Check database and destination; no existing backup was overwritten.") from None
    print("Verified private database backup:", target)


if __name__ == "__main__":
    main()
