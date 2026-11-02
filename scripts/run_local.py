"""Start the API and durable worker together; bind only to localhost by default."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
children = []


def stop(*_):
    for child in children:
        if child.poll() is None:
            child.terminate()


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
try:
    # Avoid simultaneous first-time schema creation. Initialise before worker spawn.
    from sbsm.config import Settings
    from sbsm.database import Database
    from sbsm.engine import now_ms
    settings = Settings()
    db = Database(settings.database_url)
    db.initialize(settings.initial_capital_paise, now_ms())
    db.engine.dispose()
    children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "sbsm.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000", "--no-access-log"]))
    children.append(subprocess.Popen([sys.executable, "-m", "sbsm.worker"]))
    while all(child.poll() is None for child in children):
        time.sleep(0.25)
finally:
    stop()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
