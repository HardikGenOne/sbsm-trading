from fastapi.testclient import TestClient

from sbsm.api import create_app
from sbsm.sessions import BrowserSessions, COOKIE
from conftest import enter, make_signal


def client_for(system):
    engine, clock, calendar = system
    return TestClient(create_app(engine.settings, engine.db, calendar, clock))


def test_public_demo_assets_do_not_expose_account(system):
    with client_for(system) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "Demo sandbox" in root.text
        assert "Content-Security-Policy" in root.headers
        assert "script-src 'self'" in root.headers["Content-Security-Policy"]
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/style.css").status_code == 200
        assert client.get("/api/portfolio").status_code == 401
        assert client.get("/api/summary").status_code == 401
        assert client.get("/auth/session").json() == {"authenticated": False}


def test_browser_login_cookie_csrf_and_logout(system):
    with client_for(system) as client:
        assert client.post("/auth/session", headers={"Authorization": "Bearer wrong"}).status_code == 401
        login = client.post("/auth/session", headers={"Authorization": "Bearer " + "a" * 40})
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]
        assert "no-store" == login.headers["cache-control"]
        assert "a" * 40 not in login.text
        assert client.get("/api/portfolio").status_code == 200
        csrf = login.json()["csrf_token"]
        assert client.get("/auth/session").json()["csrf_token"] == csrf
        assert client.post("/controls/entries", json={"paused": True}).status_code == 403
        assert client.post("/controls/entries", json={"paused": True}, headers={"Origin": "https://attacker.invalid", "X-CSRF-Token": csrf}).status_code == 403
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        assert client.post("/controls/entries", json={"paused": True}, headers=headers).status_code == 200
        assert client.get("/api/portfolio").json()["entries_paused"]
        assert client.delete("/auth/session", headers=headers).status_code == 200
        assert client.get("/api/portfolio").status_code == 401


def test_cookie_expiry_and_tampering(system):
    engine, clock, _ = system
    with client_for(system) as client:
        client.post("/auth/session", headers={"Authorization": "Bearer " + "a" * 40})
        cookie = client.cookies.get(COOKIE)
        client.cookies.clear()
        assert client.get("/api/portfolio", headers={"Cookie": COOKIE + "=" + cookie + "x"}).status_code == 401
        clock.advance(engine.settings.session_seconds * 1000)
        assert client.get("/api/portfolio", headers={"Cookie": COOKIE + "=" + cookie}).status_code == 401


def test_bearer_clients_and_webhooks_do_not_need_browser_csrf(system):
    engine, _, _ = system
    with client_for(system) as client:
        auth = {"Authorization": "Bearer " + "a" * 40}
        assert client.post("/controls/entries", json={"paused": True}, headers=auth).status_code == 200
        assert client.post("/webhooks/tradingview/" + "b" * 40, json=make_signal(engine).model_dump(mode="json")).status_code == 202


def test_recent_first_records_and_summary(system):
    engine, clock, _ = system
    for symbol in ("ICICIBANK", "SBIN", "BEL"):
        engine.ingest(make_signal(engine, symbol))
    engine.pause(True)
    engine.step()
    with client_for(system) as client:
        auth = {"Authorization": "Bearer " + "a" * 40}
        result = client.get("/api/signals?order=desc&limit=2", headers=auth).json()
        assert [item["id"] for item in result["items"]] == [3, 2]
        ascending = client.get("/api/signals?after=1", headers=auth).json()
        assert [item["id"] for item in ascending["items"]] == [2, 3]
        summary = client.get("/api/summary", headers=auth).json()
        assert summary == {"signals": 3, "skipped": 3, "active_orders": 0, "closed_trades": 0}


def test_session_signed_with_one_admin_secret_cannot_use_another(system):
    _, clock, _ = system
    first = BrowserSessions("a" * 40, clock)
    second = BrowserSessions("z" * 40, clock)
    cookie, _ = first.issue()
    assert first.verify(cookie)
    assert second.verify(cookie) is None


def test_signal_trace_includes_execution_and_accounting(system):
    engine, clock, _ = system
    entry = enter(engine, clock)
    with client_for(system) as client:
        assert client.get("/api/signals/1/trace").status_code == 401
        auth = {"Authorization": "Bearer " + "a" * 40}
        trace = client.get("/api/signals/1/trace", headers=auth).json()
        assert trace["signal"]["event_id"] == entry.event_id
        assert len(trace["orders"]) == len(trace["fills"]) == 1
        assert trace["position"]["quantity"] > 0
        assert {row["kind"] for row in trace["ledger"]} == {"RESERVE", "BUY_FILL", "RELEASE_RESERVE"}
        assert client.get("/api/signals/999/trace", headers=auth).status_code == 404
