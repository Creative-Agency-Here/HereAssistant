from __future__ import annotations

import pytest
from aiohttp import web

from core import config
from webapp.api.routes import crm_activity


def test_crm_activity_is_owner_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ADMIN_ID", 100)

    with pytest.raises(web.HTTPForbidden):
        crm_activity._require_owner({"user": {"id": 200}})  # type: ignore[arg-type]


def test_crm_activity_accepts_configured_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ADMIN_ID", 100)

    crm_activity._require_owner({"user": {"id": 100}})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_owner_check_happens_before_remote_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    called = False

    async def operation() -> list[object]:
        nonlocal called
        called = True
        return []

    with pytest.raises(web.HTTPForbidden):
        await crm_activity._response(  # type: ignore[arg-type]
            {"user": {"id": 200}}, operation
        )

    assert not called
