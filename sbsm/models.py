from sqlalchemy import BigInteger, Boolean, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    initial: Mapped[int] = mapped_column(BigInteger)
    cash: Mapped[int] = mapped_column(BigInteger)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    unsettled: Mapped[int] = mapped_column(BigInteger, default=0)
    realized: Mapped[int] = mapped_column(BigInteger, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciliation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_heartbeat_ms: Mapped[int] = mapped_column(BigInteger, default=0)


class Event(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(1200), unique=True)
    cycle_id: Mapped[str] = mapped_column(String(1200), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    received_ms: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(40), default="QUEUED", index=True)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)


class Conflict(Base):
    __tablename__ = "signal_conflicts"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(1200))
    received_ms: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(unique=True)
    cycle_id: Mapped[str] = mapped_column(String(1200), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    created_ms: Mapped[int] = mapped_column(BigInteger)
    eligible_ms: Mapped[int] = mapped_column(BigInteger)
    expires_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    budget: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_left: Mapped[int] = mapped_column(BigInteger, default=0)
    reference_paise: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_quote_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fixed_fee_charged: Mapped[bool] = mapped_column(Boolean, default=False)


class Position(Base):
    __tablename__ = "positions"
    cycle_id: Mapped[str] = mapped_column(String(1200), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[int] = mapped_column(BigInteger, default=0)
    realized: Mapped[int] = mapped_column(BigInteger, default=0)
    opened_ms: Mapped[int] = mapped_column(BigInteger)
    closed_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(16))
    asof_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    observed_ms: Mapped[int] = mapped_column(BigInteger)
    bid: Mapped[int] = mapped_column(BigInteger)
    ask: Mapped[int] = mapped_column(BigInteger)
    bid_quantity: Mapped[int] = mapped_column(Integer)
    ask_quantity: Mapped[int] = mapped_column(Integer)
    last: Mapped[int] = mapped_column(BigInteger)
    tick: Mapped[int] = mapped_column(Integer)
    bid_used: Mapped[int] = mapped_column(Integer, default=0)
    ask_used: Mapped[int] = mapped_column(Integer, default=0)


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("order_id", "quote_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(index=True)
    quote_id: Mapped[str] = mapped_column(String(64))
    cycle_id: Mapped[str] = mapped_column(String(1200))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(BigInteger)
    fees: Mapped[int] = mapped_column(BigInteger)
    realized: Mapped[int] = mapped_column(BigInteger, default=0)
    filled_ms: Mapped[int] = mapped_column(BigInteger)


class Ledger(Base):
    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    time_ms: Mapped[int] = mapped_column(BigInteger)
    order_id: Mapped[int | None] = mapped_column(nullable=True)
    cash_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    reserve_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    unsettled_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    realized_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    cash_after: Mapped[int] = mapped_column(BigInteger)
    reserved_after: Mapped[int] = mapped_column(BigInteger)
    unsettled_after: Mapped[int] = mapped_column(BigInteger)
    realized_after: Mapped[int] = mapped_column(BigInteger)


class Settlement(Base):
    __tablename__ = "settlements"
    id: Mapped[int] = mapped_column(primary_key=True)
    fill_id: Mapped[int] = mapped_column(unique=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    available_ms: Mapped[int] = mapped_column(BigInteger)
    released: Mapped[bool] = mapped_column(Boolean, default=False)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    time_ms: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
