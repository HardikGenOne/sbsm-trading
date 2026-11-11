"""One-time rollout of the explicitly approved inbox-only receiver identity."""
import os
import re

from sqlalchemy import text

from .database import Database
from .models import WebhookReceipt


def provision(settings, password):
    if not re.fullmatch(r"[0-9a-f]{64}", password):
        raise ValueError("Invalid private receiver password configuration")
    db = Database(settings.database_url)
    try:
        WebhookReceipt.__table__.create(db.engine, checkfirst=True)
        with db.engine.begin() as connection:
            role = connection.execute(text("SELECT rolcreaterole, rolcreatedb, rolsuper FROM pg_roles WHERE rolname='sbsm_webhook_ingress'")).first()
            if role is None:
                # Password is validated as hexadecimal before composing DDL.
                connection.exec_driver_sql("CREATE ROLE sbsm_webhook_ingress LOGIN NOINHERIT NOCREATEDB NOCREATEROLE PASSWORD '" + password + "'")
            elif any(role):
                raise ValueError("Receiver role has unexpected administrative privileges")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO sbsm_webhook_ingress")
            connection.exec_driver_sql("GRANT INSERT ON public.webhook_receipts TO sbsm_webhook_ingress")
            connection.exec_driver_sql("GRANT SELECT (id, received_ms, receipt_key), UPDATE (receipt_key) ON public.webhook_receipts TO sbsm_webhook_ingress")
            connection.exec_driver_sql("GRANT USAGE ON SEQUENCE public.webhook_receipts_id_seq TO sbsm_webhook_ingress")
            for table in ("accounts", "ledger", "signals", "orders", "positions", "quotes", "notifications"):
                readable = connection.execute(text("SELECT has_table_privilege('sbsm_webhook_ingress', :table, 'SELECT,INSERT,UPDATE,DELETE')"), {"table": "public." + table}).scalar()
                if readable:
                    raise ValueError("Receiver role has unexpected trading-table access")
    finally:
        db.engine.dispose()


def main():
    from dotenv import load_dotenv
    from .config import Settings
    from .render_service import configure

    load_dotenv("/etc/secrets/sbsm.env", override=False)
    configure(os.environ)
    try:
        provision(Settings(), os.environ["SBSM_RECEIVER_SETUP_PASSWORD"])
    except Exception:
        raise SystemExit("Inbox identity provisioning failed; inspect private configuration") from None
    print("Inbox-only receiver role configured; trading-table access denied", flush=True)


if __name__ == "__main__":
    main()
