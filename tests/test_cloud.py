from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from sbsm.api import create_app
from sbsm.config import Settings
from sbsm.service import commands
from conftest import make_signal


def test_health_requires_worker_but_not_broker_credentials(system):
    engine, clock, calendar = system
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as client:
        assert client.get("/health/ready").status_code == 503
        engine.step()
        assert client.get("/health/ready").status_code == 200
        clock.advance(31000)
        assert client.get("/health/ready").status_code == 503


def test_untrusted_hosts_and_webhook_ips_blocked(system):
    engine, clock, calendar = system
    engine.settings.webhook_allowed_ips = ["192.0.2.10"]
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as client:
        assert client.get("/health", headers={"Host": "attacker.invalid"}).status_code == 400
        url = "/webhooks/tradingview/" + "b" * 40
        assert client.post(url, json=make_signal(engine).model_dump(mode="json")).status_code == 403


def test_production_requires_explicit_secure_configuration(system):
    engine, _, _ = system
    body = engine.settings.model_dump() | {"environment": "production"}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **body)
    body |= {"session_cookie_secure": True, "public_base_url": "https://paper.example.com", "allowed_hosts": ["paper.example.com", "127.0.0.1"], "webhook_allowed_ips": ["52.89.214.238"]}
    settings = Settings(_env_file=None, **body)
    assert settings.environment == "production"
    settings.allowed_hosts = ["*"]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **settings.model_dump())


def test_supervisor_starts_notifier_only_when_enabled(system):
    engine, _, _ = system
    assert len(commands(engine.settings, "127.0.0.1", 8000)) == 2
    engine.settings.telegram_enabled = True
    configured = commands(engine.settings, "0.0.0.0", 8000)
    assert len(configured) == 3
    assert configured[-1][-1] == "sbsm.notifier"
    assert "--no-access-log" in configured[0]
