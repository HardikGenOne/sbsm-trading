from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.engine import make_url

from .models import Account, Base, Ledger


class Database:
    def __init__(self, url: str):
        sqlite = url.startswith("sqlite:")
        if sqlite:
            name = make_url(url).database
            if name and name != ":memory:":
                Path(name).parent.mkdir(parents=True, exist_ok=True)
        self.sqlite = sqlite
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 1} if sqlite else {}, pool_pre_ping=True)
        if sqlite:
            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=1000")

    def initialize(self, initial: int, now: int):
        Base.metadata.create_all(self.engine)
        with self.write() as (session, account):
            if account is None:
                account = Account(id=1, initial=initial, cash=initial, reserved=0, unsettled=0, realized=0, paused=False, reconciliation_required=False, worker_heartbeat_ms=0)
                session.add(account)
                session.add(Ledger(kind="INITIAL_DEPOSIT", time_ms=now, cash_delta=initial, reserve_delta=0, unsettled_delta=0, realized_delta=0, cash_after=initial, reserved_after=0, unsettled_after=0, realized_after=0))

    @contextmanager
    def write(self):
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                if self.sqlite:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                account = session.scalar(select(Account).where(Account.id == 1).with_for_update())
                yield session, account
                session.commit()
            except BaseException:
                session.rollback()
                raise

    @contextmanager
    def read(self):
        with Session(self.engine) as session:
            yield session
