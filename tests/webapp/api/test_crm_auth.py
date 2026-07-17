from __future__ import annotations

import json

import pytest

from core import config, herecrm_client
from webapp.api.routes import crm_auth


class FakeRequest:
    def __init__(self, body: object):
        self.body = body

    async def json(self) -> object:
        return self.body


@pytest.mark.asyncio
async def test_exchange_sets_secure_httponly_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_test_secret")
    monkeypatch.setattr(config, "ADMIN_ID", 42)

    async def exchange(ticket: str) -> dict[str, object]:
        assert ticket == f"hat_{'a' * 64}"
        return {"userId": 7, "tenantId": "tenant-id"}

    monkeypatch.setattr(herecrm_client, "exchange_sso_ticket", exchange)
    response = await crm_auth.exchange_handler(  # type: ignore[arg-type]
        FakeRequest({"ticket": f"hat_{'a' * 64}"})
    )

    assert response.status == 200
    cookie = response.cookies["ha_crm_session"]
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Lax"


@pytest.mark.asyncio
async def test_exchange_rejects_bad_ticket_without_crm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def exchange(_ticket: str) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(herecrm_client, "exchange_sso_ticket", exchange)
    response = await crm_auth.exchange_handler(  # type: ignore[arg-type]
        FakeRequest({"ticket": "bad"})
    )

    assert response.status == 400
    assert json.loads(response.text)["error"] == "bad_ticket"
    assert not called


@pytest.mark.asyncio
async def test_public_config_contains_urls_but_not_sync_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "HERECRM_SYNC_URL", "https://api.example.com/api/v1"
    )
    monkeypatch.setattr(config, "HERECRM_WEB_URL", "https://crm.example.com")
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_must_stay_server_side")

    response = await crm_auth.config_handler(None)  # type: ignore[arg-type]
    body = json.loads(response.text)

    assert body == {
        "crmApiBase": "https://crm.example.com/api/v1",
        "crmWebUrl": "https://crm.example.com",
    }
    assert "has_must_stay_server_side" not in response.text
