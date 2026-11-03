"""Render entry point: derive the public origin and require durable SQL storage."""
import json
import os

from sqlalchemy.engine import make_url

from .config import Settings
from .service import supervise

TV_IPS = ["52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"]


def configure(environment):
    hostname = environment.get("RENDER_EXTERNAL_HOSTNAME", "")
    if not hostname or "/" in hostname or ":" in hostname:
        raise ValueError("Render must provide its external hostname")
    url = make_url(environment.get("SBSM_DATABASE_URL", ""))
    if url.get_backend_name() != "postgresql" or url.query.get("sslmode") not in ("require", "verify-full"):
        raise ValueError("Render requires durable Postgres with TLS; SQLite is not permitted")
    environment["SBSM_ENVIRONMENT"] = "production"
    environment["SBSM_SESSION_COOKIE_SECURE"] = "true"
    environment["SBSM_PUBLIC_BASE_URL"] = "https://" + hostname
    environment["SBSM_ALLOWED_HOSTS"] = json.dumps([hostname, "127.0.0.1", "localhost"])
    environment.setdefault("SBSM_WEBHOOK_ALLOWED_IPS", json.dumps(TV_IPS))
    # No wildcard trust for user-controlled forwarded IPs. Webhook ingress stays
    # fail closed until the hosted proxy chain has been verified with TradingView.
    environment.setdefault("SBSM_FORWARDED_ALLOW_IPS", "127.0.0.1")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    environment["SBSM_DATABASE_URL"] = url.render_as_string(hide_password=False)
    return environment


def main():
    try:
        configure(os.environ)
        settings = Settings()
    except Exception:
        raise SystemExit("Render configuration incomplete: check private SQL credentials and public origin") from None
    raise SystemExit(supervise(settings, "0.0.0.0", int(os.environ.get("PORT", "10000"))))


if __name__ == "__main__":
    main()
