"""Exchange a manually obtained Kite request token for market-data access.

Uses the official login flow. Never stores the API secret or automates TOTP/login.
"""
import getpass
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import httpx

from sbsm.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def save_session(api_key, access_token):
    path = ROOT / ".env"
    original = path.read_text()
    values = {"SBSM_KITE_API_KEY": api_key, "SBSM_KITE_ACCESS_TOKEN": access_token}
    lines = []
    for line in original.splitlines():
        key = line.split("=", 1)[0]
        if key in values:
            line = key + "=" + json.dumps(values.pop(key))
        lines.append(line)
    lines.extend(key + "=" + json.dumps(value) for key, value in values.items())
    temporary = ROOT / ".env.session.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as file:
            file.write("\n".join(lines) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    settings = Settings()
    api_key = settings.kite_api_key.get_secret_value() or getpass.getpass("Kite API key: ").strip()
    print("Open this official login URL and log in manually:")
    print("https://kite.zerodha.com/connect/login?" + urlencode({"v": "3", "api_key": api_key}))
    print("Copy request_token from your registered redirect URL after login.")
    request_token = getpass.getpass("Request token (hidden): ").strip()
    api_secret = getpass.getpass("API secret (hidden; not stored): ").strip()
    checksum = hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()
    try:
        response = httpx.post("https://api.kite.trade/session/token", headers={"X-Kite-Version": "3"}, data={"api_key": api_key, "request_token": request_token, "checksum": checksum}, timeout=10)
        response.raise_for_status()
        access_token = response.json()["data"]["access_token"]
        save_session(api_key, access_token)
    except (httpx.HTTPError, KeyError, ValueError):
        raise SystemExit("Session exchange failed. Check app credentials and use a fresh request token; no response secrets were printed.")
    print("Market-data session saved privately in .env. Restart the API and worker to load it.")
    print("No order was placed. This project contains paper execution only.")


if __name__ == "__main__":
    main()
