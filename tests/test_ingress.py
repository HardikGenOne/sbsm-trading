from fastapi.testclient import TestClient
import pytest

from sbsm.api import create_app
from conftest import make_signal


@pytest.mark.parametrize("headers", [
    {}, {"cf-connecting-ip": "invalid"},
    {"cf-connecting-ip": "52.89.214.238, 1.2.3.4"},
    [("cf-connecting-ip", "52.89.214.238"), ("cf-connecting-ip", "1.2.3.4")],
    {"x-forwarded-for": "52.89.214.238"},
])
def test_render_rejects_missing_invalid_and_ambiguous_headers(system, headers):
    engine, clock, calendar = system
    engine.settings.webhook_client_ip_source = "render_cloudflare"
    engine.settings.webhook_allowed_ips = ["52.89.214.238"]
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as c:
        assert c.post('/webhooks/tradingview/' + 'b'*40, json=make_signal(engine).model_dump(mode='json'), headers=headers).status_code == 403


def test_render_requires_token_and_explicit_allowlist(system):
    engine, clock, calendar = system
    engine.settings.webhook_client_ip_source = "render_cloudflare"
    engine.settings.webhook_allowed_ips = ["52.89.214.238"]
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as c:
        body = make_signal(engine).model_dump(mode='json')
        headers = {"cf-connecting-ip": "52.89.214.238"}
        assert c.post('/webhooks/tradingview/wrong', json=body, headers=headers).status_code == 401
        assert c.post('/webhooks/tradingview/' + 'b'*40, json=body, headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 403
        assert c.post('/webhooks/tradingview/' + 'b'*40, json=body, headers=headers).status_code == 202
        assert c.get('/api/ingress').status_code == 401


def test_local_mode_does_not_trust_cloudflare_header(system):
    engine, clock, calendar = system
    engine.settings.webhook_allowed_ips = ["52.89.214.238"]
    with TestClient(create_app(engine.settings, engine.db, calendar, clock)) as c:
        assert c.post('/webhooks/tradingview/' + 'b'*40, json=make_signal(engine).model_dump(mode='json'), headers={"cf-connecting-ip": "52.89.214.238"}).status_code == 403
