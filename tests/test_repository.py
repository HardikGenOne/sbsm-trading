import pytest

from sbsm.models import Account, Event
from sbsm.repository import count, find, grouped_counts, one, total
from conftest import make_signal


def test_repository_filters_sorting_aggregation_and_identity(system):
    engine, clock, _ = system
    for symbol in ("BEL", "BHEL", "NTPC"):
        engine.ingest(make_signal(engine, symbol))
        clock.advance()
    with engine.db.write() as (session, account):
        rows = find(session, Event, {"$or": [{"symbol": "BEL"}, {"id": {"$gte": 2}}]}, sort=[("id", -1)], offset=1, limit=1)
        assert len(rows) == 1 and rows[0].symbol == "BHEL"
        assert one(session, Event, {"id": 2}) is rows[0]
        assert count(session, Event, {"symbol": {"$in": ["BEL", "NTPC"]}}) == 2
        assert count(session, Event, {"reason": {"$ne": None}}) == 0
        assert total(session, Account, "cash") == account.initial
        assert grouped_counts(session, Event, "status") == {"QUEUED": 3}
        rows[0].status = "SKIPPED"
        assert count(session, Event, {"status": "SKIPPED"}) == 1
        with pytest.raises(ValueError, match="Unsupported"):
            find(session, Event, {"id": {"$where": "unsafe"}})
