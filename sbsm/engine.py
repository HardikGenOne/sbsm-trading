"""Transactional, long-only portfolio state machine. All money is integer paise."""
import hashlib
import json
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, func
from sqlalchemy.orm import object_session

from .models import Account, Audit, Conflict, Event, FeedPreference, Fill, Ledger, Order, Position, Quote, Settlement
from .schemas import QuoteInput, Signal
from .notifications import enqueue, rupees

ACTIVE = ("PENDING", "PARTIALLY_FILLED")


def now_ms():
    return time.time_ns() // 1_000_000


def paise(value):
    return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def fee(notional: int, bps: int):
    return (notional * bps + 9999) // 10000


def serialized(signal):
    # Decimal normalisation keeps integer and decimal representations equivalent.
    body = signal.model_dump(mode="json")
    body["reference_price"] = format(signal.reference_price.normalize(), "f")
    return body


def fingerprint(body):
    stable = {k: v for k, v in body.items() if k != "emitted_at_ms"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SignalConflict(Exception):
    pass


class RateLimited(Exception):
    pass


class PaperEngine:
    def __init__(self, db, settings, calendar, clock=now_ms):
        self.db, self.settings, self.calendar, self.clock = db, settings, calendar, clock

    def active_feed(self, session):
        preference = session.get(FeedPreference, 1)
        if preference:
            return preference.provider, preference.selected_ms
        return self.settings.feed_mode, 0

    def select_feed(self, provider):
        with self.db.write() as (s, _):
            current, selected_ms = self.active_feed(s)
            if provider == current:
                return
            now = self.clock()
            s.merge(FeedPreference(id=1, provider=provider, selected_ms=max(now, selected_ms + 1)))
            s.add(Audit(time_ms=now, action="SELECT_QUOTE_FEED", detail=provider))

    def ingest(self, signal: Signal, received_ms=None):
        now = self.clock()
        body = serialized(signal)
        digest = fingerprint(body)
        conflict = False
        with self.db.write() as (s, account):
            previous = s.scalar(select(Event).where(Event.event_id == signal.event_id))
            if previous:
                result = {"sequence": previous.id, "status": previous.status, "duplicate": True}
                if previous.fingerprint != digest:
                    s.add(Conflict(event_id=signal.event_id, received_ms=now, payload=body))
                    conflict = True
            else:
                recent = s.scalar(select(func.count()).select_from(Event).where(Event.received_ms >= now - 60_000))
                if recent >= 120:
                    raise RateLimited("At most 120 new signals per minute")
                # v1.0.0 Pine's unsupported carriage-return escape encoded each
                # lowercase r as a JSON carriage-return escape. After decoding,
                # accept only the exact approved config in that legacy spelling; preserve
                # event/cycle IDs so existing alert snapshots can still exit.
                approved = self.settings.approved_config
                if signal.config_key not in {approved, approved.replace("r", chr(13))}:
                    raise ValueError("Unapproved strategy configuration")
                if signal.emitted_at_ms > now + 5000:
                    raise ValueError("Signal timestamp is in the future")
                event = Event(event_id=signal.event_id, cycle_id=signal.trade_cycle_id, symbol=signal.symbol, action=signal.action, payload=body, fingerprint=digest, received_ms=now if received_ms is None else received_ms, status="QUEUED", decision={})
                s.add(event)
                s.flush()
                result = {"sequence": event.id, "status": "QUEUED", "duplicate": False}
        if conflict:
            raise SignalConflict("Event ID already exists with different content; quarantined")
        return result

    def record(self, s, a, kind, now, order_id=None, cash=0, reserve=0, unsettled=0, realized=0):
        a.cash += cash
        a.reserved += reserve
        a.unsettled += unsettled
        a.realized += realized
        if min(a.cash, a.reserved, a.unsettled, a.cash - a.reserved - a.unsettled) < 0:
            raise RuntimeError("Accounting invariant violated; transaction rolled back")
        s.add(Ledger(kind=kind, time_ms=now, order_id=order_id, cash_delta=cash, reserve_delta=reserve, unsettled_delta=unsettled, realized_delta=realized, cash_after=a.cash, reserved_after=a.reserved, unsettled_after=a.unsettled, realized_after=a.realized))

    def quote(self, value: QuoteInput, source: str, selection=None):
        now = self.clock()
        if value.asof_ms > now + 2000:
            raise ValueError("Quote timestamp is in the future")
        digest = hashlib.sha256((source + value.model_dump_json()).encode()).hexdigest()
        with self.db.write() as (s, _):
            if selection is not None and selection != self.active_feed(s):
                return None
            if not s.get(Quote, digest):
                s.add(Quote(id=digest, symbol=value.symbol, source=source, asof_ms=value.asof_ms, observed_ms=now, bid=paise(value.bid), ask=paise(value.ask), bid_quantity=value.bid_quantity, ask_quantity=value.ask_quantity, last=paise(value.last_price), tick=max(1, paise(value.tick_size)), bid_used=0, ask_used=0))
        return digest

    def skip(self, event, reason, **details):
        event.status = "SKIPPED"
        event.reason = reason
        event.decision = details
        lines = [f"{event.symbol} · {event.action}", f"Reason: {reason}", f"Signal #{event.id}"]
        if "required_paise" in details:
            lines += [f"Required: {rupees(details['required_paise'])}", f"Available: {rupees(details['available_paise'])}"]
        enqueue(object_session(event), self.settings, f"signal:{event.id}:skipped", "SIGNAL SKIPPED", lines, self.clock())

    def cancel(self, s, a, order, now, reason):
        if order.status not in ACTIVE:
            return
        if order.reserved_left:
            self.record(s, a, "RELEASE_RESERVE", now, order.id, reserve=-order.reserved_left)
            order.reserved_left = 0
        order.status = "EXPIRED" if reason == "ENTRY_EXPIRED" else "CANCELLED"
        order.reason = reason
        event = s.get(Event, order.signal_id)
        event.status = "PARTIAL" if order.filled_quantity else order.status
        event.reason = reason
        enqueue(s, self.settings, f"order:{order.id}:cancelled", "ENTRY " + order.status, [order.symbol, f"Reason: {reason}", f"Already filled: {order.filled_quantity} shares", f"Order #{order.id}"], now)

    def exit_intent(self, s, a, event, now):
        # A queued entry whose exit has already arrived must never open a late position.
        queued = s.scalars(select(Event).where(Event.cycle_id == event.cycle_id, Event.action == "ENTER_LONG", Event.status == "QUEUED")).all()
        for entry in queued:
            self.skip(entry, "EXIT_ALREADY_RECEIVED")
        buys = s.scalars(select(Order).where(Order.cycle_id == event.cycle_id, Order.side == "BUY", Order.status.in_(ACTIVE))).all()
        for order in buys:
            self.cancel(s, a, order, now, "EXIT_RECEIVED")
        position = s.get(Position, event.cycle_id)
        if not position or position.quantity == 0:
            other = s.scalar(select(Position).where(Position.symbol == event.symbol, Position.quantity > 0))
            if other:
                a.reconciliation_required = True
                self.skip(event, "UNMATCHED_EXIT_WITH_OPEN_POSITION", open_cycle_id=other.cycle_id)
                s.add(Audit(time_ms=now, action="RECONCILIATION_REQUIRED", detail=f"Unmatched exit for {event.symbol}; new entries blocked"))
            else:
                self.skip(event, "NO_MATCHING_POSITION")
            return
        existing = s.scalar(select(Order).where(Order.cycle_id == event.cycle_id, Order.side == "SELL", Order.status.in_(ACTIVE)))
        if existing:
            self.skip(event, "EXIT_ALREADY_PENDING", order_id=existing.id)
            return
        order = Order(signal_id=event.id, cycle_id=event.cycle_id, symbol=event.symbol, side="SELL", status="PENDING", created_ms=now, eligible_ms=now + self.settings.execution_latency_ms, expires_ms=None, budget=0, reserved_left=0, reference_paise=paise(event.payload["reference_price"]), quantity=position.quantity, filled_quantity=0)
        s.add(order)
        event.status = "ACCEPTED"
        event.decision = {"quantity": position.quantity}
        enqueue(s, self.settings, f"signal:{event.id}:exit", "EXIT QUEUED", [event.symbol, f"Quantity: {position.quantity} shares", "Waiting for an open market and a fresh quote.", f"Signal #{event.id}"], now)

    def entry_intent(self, s, a, event, now, market_open):
        pool = a.initial + a.realized
        budget = max(0, pool * self.settings.allocation_bps // 10000)
        available = a.cash - a.reserved - a.unsettled
        details = {"pool_paise": pool, "required_paise": budget, "available_paise": available, "reference_price": event.payload["reference_price"]}
        reason = None
        if now - event.payload["signal_bar_close_ms"] > self.settings.signal_max_age_seconds * 1000 or now >= event.received_ms + self.settings.entry_ttl_seconds * 1000:
            reason = "STALE_ENTRY"
        elif a.paused:
            reason = "ENTRIES_PAUSED"
        elif a.reconciliation_required:
            reason = "RECONCILIATION_REQUIRED"
        elif not market_open:
            reason = "MARKET_CLOSED"
        elif s.scalar(select(Position).where(Position.symbol == event.symbol, Position.quantity > 0)) or s.scalar(select(Order).where(Order.symbol == event.symbol, Order.side == "BUY", Order.status.in_(ACTIVE))):
            reason = "POSITION_OR_ENTRY_EXISTS"
        elif s.scalar(select(Order).where(Order.cycle_id == event.cycle_id)) or s.scalar(select(Event).where(Event.cycle_id == event.cycle_id, Event.action == "EXIT_LONG")):
            reason = "CYCLE_ALREADY_USED_OR_EXITED"
        elif budget <= 0 or available < budget:
            reason = "INSUFFICIENT_FUNDS"
        if reason:
            self.skip(event, reason, **details)
            return
        order = Order(signal_id=event.id, cycle_id=event.cycle_id, symbol=event.symbol, side="BUY", status="PENDING", created_ms=now, eligible_ms=max(now, event.received_ms + self.settings.execution_latency_ms), expires_ms=event.received_ms + self.settings.entry_ttl_seconds * 1000, budget=budget, reserved_left=budget, reference_paise=paise(event.payload["reference_price"]), quantity=0, filled_quantity=0)
        s.add(order)
        s.flush()
        self.record(s, a, "RESERVE", now, order.id, reserve=budget)
        event.status = "ACCEPTED"
        event.decision = details
        enqueue(s, self.settings, f"signal:{event.id}:accepted", "ENTRY ACCEPTED", [event.symbol, f"Budget reserved: {rupees(budget)}", f"Fund pool: {rupees(pool)}", f"Available after reserve: {rupees(available - budget)}", f"Signal #{event.id}"], now)

    def fresh_quote(self, s, order, now, selection):
        provider, selected_ms = selection
        q = s.scalar(select(Quote).where(Quote.symbol == order.symbol, Quote.source == provider, Quote.observed_ms >= selected_ms).order_by(Quote.asof_ms.desc(), Quote.observed_ms.desc()))
        if not q or q.id == order.last_quote_id:
            return None
        if not 0 <= now - q.asof_ms <= self.settings.quote_max_age_seconds * 1000:
            return None
        if q.observed_ms < order.eligible_ms or now < order.eligible_ms:
            return None
        return q

    def fill_order(self, s, a, order, now, selection):
        if order.expires_ms is not None and now >= order.expires_ms:
            self.cancel(s, a, order, now, "ENTRY_EXPIRED")
            return
        q = self.fresh_quote(s, order, now, selection)
        if not q:
            return
        cfg = self.settings
        buy = order.side == "BUY"
        # Adverse slippage, rounded conservatively to the instrument tick.
        if buy:
            raw = (q.ask * (10000 + cfg.slippage_bps) + 9999) // 10000
            price = ((raw + q.tick - 1) // q.tick) * q.tick
            cap = order.reference_paise * (10000 + cfg.price_protection_bps) // 10000
            if price > cap:
                return  # Protected entry waits; it expires without chasing prices.
            depth = max(0, q.ask_quantity * cfg.depth_participation_bps // 10000 - q.ask_used)
            if not order.quantity:
                order.quantity = order.budget * 10000 // (max(cap, price) * (10000 + cfg.buy_cost_bps))
                if order.quantity < 1:
                    self.cancel(s, a, order, now, "BUDGET_BELOW_ONE_SHARE")
                    event = s.get(Event, order.signal_id)
                    self.skip(event, "BUDGET_BELOW_ONE_SHARE", **event.decision)
                    return
            affordable = order.reserved_left * 10000 // (price * (10000 + cfg.buy_cost_bps))
            quantity = min(order.quantity - order.filled_quantity, depth, affordable)
            if quantity <= 0:
                return
            fees = fee(price * quantity, cfg.buy_cost_bps)
            cost = price * quantity + fees
            position = s.get(Position, order.cycle_id)
            if not position:
                position = Position(cycle_id=order.cycle_id, symbol=order.symbol, quantity=0, cost=0, realized=0, opened_ms=now)
                s.add(position)
            position.quantity += quantity
            position.cost += cost
            q.ask_used += quantity
            order.reserved_left -= cost
            self.record(s, a, "BUY_FILL", now, order.id, cash=-cost, reserve=-cost)
            realized = 0
        else:
            position = s.get(Position, order.cycle_id)
            if not position or not position.quantity:
                self.cancel(s, a, order, now, "POSITION_ALREADY_CLOSED")
                return
            raw = q.bid * (10000 - cfg.slippage_bps) // 10000
            price = (raw // q.tick) * q.tick
            depth = max(0, q.bid_quantity * cfg.depth_participation_bps // 10000 - q.bid_used)
            quantity = min(position.quantity, order.quantity - order.filled_quantity, depth)
            if price <= 0 or quantity <= 0:
                return
            fees = fee(price * quantity, cfg.sell_cost_bps)
            if not order.fixed_fee_charged:
                fees += cfg.sell_fixed_cost_paise
            proceeds = price * quantity - fees
            # Avoid manufacturing a negative cash credit on a tiny partial fill.
            if proceeds < 0:
                return
            allocated_cost = position.cost if quantity == position.quantity else position.cost * quantity // position.quantity
            realized = proceeds - allocated_cost
            position.quantity -= quantity
            position.cost -= allocated_cost
            position.realized += realized
            if not position.quantity:
                position.closed_ms = now
            q.bid_used += quantity
            order.fixed_fee_charged = True
            hold = proceeds if cfg.settlement_mode == "next_session" else 0
            self.record(s, a, "SELL_FILL", now, order.id, cash=proceeds, unsettled=hold, realized=realized)
        fill = Fill(order_id=order.id, quote_id=q.id, cycle_id=order.cycle_id, symbol=order.symbol, side=order.side, quantity=quantity, price=price, fees=fees, realized=realized, filled_ms=now)
        s.add(fill)
        s.flush()
        if not buy and cfg.settlement_mode == "next_session":
            s.add(Settlement(fill_id=fill.id, amount=proceeds, available_ms=self.calendar.next_session_open(now), released=False))
        order.filled_quantity += quantity
        order.last_quote_id = q.id
        event = s.get(Event, order.signal_id)
        if order.filled_quantity == order.quantity:
            order.status = "FILLED"
            event.status = "FILLED"
            if order.reserved_left:
                self.record(s, a, "RELEASE_RESERVE", now, order.id, reserve=-order.reserved_left)
                order.reserved_left = 0
        else:
            order.status = "PARTIALLY_FILLED"
            event.status = "PARTIALLY_FILLED"
        title = f"{order.side} " + ("FILLED" if order.status == "FILLED" else "PARTIAL FILL")
        lines = [f"{order.symbol} · {quantity} shares @ {rupees(price)}", f"Modeled charges: {rupees(fees)}", f"Order #{order.id} · {order.filled_quantity}/{order.quantity} filled", f"Quote source: {q.source.upper()}"]
        if not buy:
            lines += [f"Net realized on this fill: {rupees(realized)}", f"Fund pool: {rupees(a.initial + a.realized)}", f"Next 12% allocation: {rupees(max(0, (a.initial + a.realized) * cfg.allocation_bps // 10000))}"]
        enqueue(s, cfg, f"fill:{fill.id}", title, lines, now)

    def step(self):
        now = self.clock()
        market_open = self.calendar.is_open(now)
        with self.db.write() as (s, a):
            selection = self.active_feed(s)
            a.worker_heartbeat_ms = now
            due = s.scalars(select(Settlement).where(Settlement.released == False, Settlement.available_ms <= now)).all()  # noqa: E712
            for item in due:
                self.record(s, a, "SETTLEMENT_RELEASE", now, unsettled=-item.amount)
                item.released = True
            # Exits are urgent; entries retain their durable receipt sequence.
            for event in s.scalars(select(Event).where(Event.status == "QUEUED", Event.action == "EXIT_LONG").order_by(Event.id)).all():
                self.exit_intent(s, a, event, now)
                s.flush()
            if market_open:
                for order in s.scalars(select(Order).where(Order.side == "SELL", Order.status.in_(ACTIVE)).order_by(Order.id)).all():
                    self.fill_order(s, a, order, now, selection)
                    s.flush()
            for event in s.scalars(select(Event).where(Event.status == "QUEUED", Event.action == "ENTER_LONG").order_by(Event.id)).all():
                self.entry_intent(s, a, event, now, market_open)
                s.flush()
            for order in s.scalars(select(Order).where(Order.side == "BUY", Order.status.in_(ACTIVE)).order_by(Order.id)).all():
                if market_open:
                    self.fill_order(s, a, order, now, selection)
                elif now >= order.expires_ms:
                    self.cancel(s, a, order, now, "ENTRY_EXPIRED")
                s.flush()
            # Conservation checks catch double spending, missing reservations, and bad P&L.
            costs = s.scalar(select(func.coalesce(func.sum(Position.cost), 0)))
            reserves = s.scalar(select(func.coalesce(func.sum(Order.reserved_left), 0)))
            if a.cash + costs != a.initial + a.realized or a.reserved != reserves:
                raise RuntimeError("Portfolio conservation invariant failed")

    def watch_symbols(self):
        with self.db.read() as s:
            pending = s.scalars(select(Order.symbol).where(Order.status.in_(ACTIVE))).all()
            owned = s.scalars(select(Position.symbol).where(Position.quantity > 0)).all()
            return sorted(set(pending + owned))

    def portfolio(self):
        now = self.clock()
        with self.db.read() as s:
            a = s.get(Account, 1)
            provider, selected_ms = self.active_feed(s)
            positions = []
            all_marked = True
            market_value = 0
            for p in s.scalars(select(Position).where(Position.quantity > 0).order_by(Position.symbol)):
                q = s.scalar(select(Quote).where(Quote.symbol == p.symbol, Quote.source == provider, Quote.observed_ms >= selected_ms).order_by(Quote.asof_ms.desc(), Quote.observed_ms.desc()))
                stale = not q or not 0 <= now - q.asof_ms <= self.settings.quote_max_age_seconds * 1000
                all_marked = all_marked and not stale
                value = q.last * p.quantity if q else None
                if value is not None:
                    market_value += value
                positions.append({"symbol": p.symbol, "cycle_id": p.cycle_id, "quantity": p.quantity, "cost_paise": p.cost, "market_value_paise": value, "unrealized_paise": None if value is None else value - p.cost, "quote_stale": stale, "quote_asof_ms": q.asof_ms if q else None})
            return {"mode": "paper", "feed_mode": provider, "currency": "INR", "money_unit": "paise", "initial_paise": a.initial, "pool_paise": a.initial + a.realized, "realized_net_paise": a.realized, "cash_paise": a.cash, "reserved_paise": a.reserved, "unsettled_paise": a.unsettled, "available_paise": a.cash - a.reserved - a.unsettled, "next_allocation_paise": max(0, (a.initial + a.realized) * self.settings.allocation_bps // 10000), "equity_paise": a.cash + market_value if all_marked else None, "valuation_complete_and_fresh": all_marked, "entries_paused": a.paused, "reconciliation_required": a.reconciliation_required, "worker_heartbeat_ms": a.worker_heartbeat_ms, "positions": positions}

    def pause(self, paused: bool):
        with self.db.write() as (s, a):
            a.paused = paused
            s.add(Audit(time_ms=self.clock(), action="PAUSE_ENTRIES" if paused else "RESUME_ENTRIES", detail="Existing accepted orders and exits remain active"))

    def cancel_entry(self, order_id):
        with self.db.write() as (s, a):
            order = s.get(Order, order_id)
            if not order or order.side != "BUY":
                raise ValueError("Entry order not found")
            self.cancel(s, a, order, self.clock(), "MANUAL_CANCEL")
            s.add(Audit(time_ms=self.clock(), action="CANCEL_ENTRY", detail=f"Order {order_id}"))

    def request_close(self, cycle_id: str):
        """Authenticated operator exit: still uses quotes, latency, and the durable worker."""
        now = self.clock()
        with self.db.write() as (s, a):
            p = s.get(Position, cycle_id)
            if not p or p.quantity <= 0:
                raise ValueError("Open paper position not found")
            pending = s.scalar(select(Event).where(Event.cycle_id == cycle_id, Event.action == "EXIT_LONG", Event.status.in_(("QUEUED", "ACCEPTED", "PARTIALLY_FILLED"))))
            if pending:
                return {"sequence": pending.id, "already_pending": True}
            event = Event(event_id="operator:" + str(uuid.uuid4()), cycle_id=cycle_id, symbol=p.symbol, action="EXIT_LONG", payload={"origin": "operator", "reference_price": str(Decimal(p.cost) / p.quantity / 100)}, fingerprint="operator", received_ms=now, status="QUEUED", decision={})
            s.add(event)
            s.flush()
            s.add(Audit(time_ms=now, action="MANUAL_CLOSE_REQUEST", detail=f"Signal sequence {event.id} for {p.symbol}"))
            return {"sequence": event.id, "already_pending": False}

    def acknowledge_reconciliation(self, note: str):
        with self.db.write() as (s, a):
            a.reconciliation_required = False
            s.add(Audit(time_ms=self.clock(), action="RECONCILIATION_ACKNOWLEDGED", detail=note))
