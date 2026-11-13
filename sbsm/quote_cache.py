"""Single-host coordination for the supervised API and quote worker.

Only disposable market data is cached here. The trading ledger stays in SQL.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time


@contextmanager
def locked_snapshot(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_suffix('.lock')
    with open(lock_path, 'a', encoding='utf8') as lock:
        os.chmod(lock_path, 0o600)
        deadline = time.monotonic() + 12
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another broker request is still running')
                time.sleep(0.05)
        try:
            try:
                state = json.loads(path.read_text())
                if not isinstance(state, dict):
                    state = {}
            except (FileNotFoundError, ValueError):
                state = {}
            try:
                yield state
            finally:
                # Replace atomically so readers never see a partial quote file.
                fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.quote-')
                try:
                    with os.fdopen(fd, 'w') as target:
                        json.dump(state, target)
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
