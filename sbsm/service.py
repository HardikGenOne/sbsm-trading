"""One supervised API/worker/notifier instance for a single persistent volume."""
import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time

import httpx

from .config import Settings
from .database import Database
from .engine import now_ms

log = logging.getLogger("sbsm.service")


def commands(settings, host, port):
    result = [
        [sys.executable, "-m", "uvicorn", "sbsm.api:create_app", "--factory", "--workers", "1", "--host", host, "--port", str(port), "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", settings.forwarded_allow_ips],
        [sys.executable, "-m", "sbsm.worker"],
    ]
    if settings.telegram_enabled:
        result.append([sys.executable, "-m", "sbsm.notifier"])
    return result


def supervise(settings, host, port):
    db = Database(settings.database_url)
    db.initialize(settings.initial_capital_paise, now_ms())
    db.engine.dispose()
    stop = threading.Event()
    children = []
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    failed = False
    started, last_check, misses = time.monotonic(), 0.0, 0
    try:
        for command in commands(settings, host, port):
            children.append(subprocess.Popen(command))
        with httpx.Client(timeout=3, trust_env=False) as client:
            while not stop.wait(0.25):
                if any(child.poll() is not None for child in children):
                    log.error("A supervised process exited; stopping the service for a clean restart")
                    failed = True
                    break
                current = time.monotonic()
                if current - started > 60 and current - last_check >= 10:
                    last_check = current
                    try:
                        healthy = client.get(f"http://127.0.0.1:{port}/health/ready").status_code == 200
                    except httpx.HTTPError:
                        healthy = False
                    misses = 0 if healthy else misses + 1
                    if misses >= 3:
                        log.error("Operational health checks failed; stopping for a clean restart")
                        failed = True
                        break
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(supervise(Settings(), args.host, args.port))


if __name__ == "__main__":
    main()
