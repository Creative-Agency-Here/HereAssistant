from __future__ import annotations

import pytest

from core import config
from webapp.api import browser_session


@pytest.fixture(autouse=True)
def sso_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_test_secret")
    monkeypatch.setattr(config, "ADMIN_ID", 42)


def test_signed_session_restores_local_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_session.time, "time", lambda: 1_000)

    token = browser_session.issue(crm_user_id=7, tenant_id="tenant-id")
    user = browser_session.read(token)

    assert user == {
        "id": 42,
        "first_name": "HereCRM",
        "username": "herecrm",
        "auth_source": "crm",
        "crm_user_id": 7,
        "tenant_id": "tenant-id",
    }


def test_tampered_session_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_session.time, "time", lambda: 1_000)
    token = browser_session.issue(crm_user_id=7, tenant_id="tenant-id")

    assert browser_session.read(f"x{token[1:]}") is None


def test_expired_session_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_000
    monkeypatch.setattr(browser_session.time, "time", lambda: now)
    token = browser_session.issue(crm_user_id=7, tenant_id="tenant-id")
    monkeypatch.setattr(
        browser_session.time,
        "time",
        lambda: now + browser_session.SESSION_TTL_SECONDS + 1,
    )

    assert browser_session.read(token) is None


def test_rotating_sync_token_invalidates_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_session.time, "time", lambda: 1_000)
    token = browser_session.issue(crm_user_id=7, tenant_id="tenant-id")
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_rotated_secret")

    assert browser_session.read(token) is None
