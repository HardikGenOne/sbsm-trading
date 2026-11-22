"""Explicit additive migrations for notifications and encrypted broker credentials."""
from sqlalchemy import Column, MetaData, String, Table, inspect, insert, select

from .models import Notification, NotificationState, BrokerCredential

REVISION = "0002_notification_outbox"
AUTH_REVISION = "0003_broker_credentials"
OUTBOX_TABLES = (Notification.__table__, NotificationState.__table__)
NEW_TABLES = (*OUTBOX_TABLES, BrokerCredential.__table__)
VERSIONS = Table("schema_migrations", MetaData(), Column("revision", String(80), primary_key=True))


def migrate_notifications(engine):
    with engine.begin() as connection:
        VERSIONS.create(connection, checkfirst=True)
        revisions = set(connection.scalars(select(VERSIONS.c.revision)))
        if revisions - {REVISION, AUTH_REVISION}:
            raise RuntimeError("Database has unsupported migrations; do not downgrade the running application")
        if REVISION not in revisions:
            for table in OUTBOX_TABLES:
                table.create(connection, checkfirst=True)
            connection.execute(insert(VERSIONS).values(revision=REVISION))
        if AUTH_REVISION not in revisions:
            BrokerCredential.__table__.create(connection, checkfirst=True)
            connection.execute(insert(VERSIONS).values(revision=AUTH_REVISION))
        inspector = inspect(connection)
        for table in NEW_TABLES:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            if existing != set(table.columns.keys()):
                raise RuntimeError("Application schema mismatch; explicit repair/migration required")
