"""Транспорт /rc: default-deny конфигурация, HTTPS как источник истины, WS-wakeup опционален."""

from __future__ import annotations

import pytest

from core.remote_control import config
from core.remote_control.control_plane_client import (
    ControlPlaneClient,
    ControlPlaneError,
    WakeupListener,
)
from core.remote_control.credential_store import DeviceCredential


def test_control_plane_url_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RC_CONTROL_PLANE_URL", raising=False)
    assert config.control_plane_url() == ""
    assert config.configured() is False


def test_control_plane_url_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RC_CONTROL_PLANE_URL", "  https://crm.example.com/api/  ")
    assert config.control_plane_url() == "https://crm.example.com/api"
    assert config.configured() is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", False),
        ("http://crm.example.com", False),  # только https
        ("ftp://crm.example.com", False),
        ("https://", False),
        ("https://crm.example.com/api/v1", True),
    ],
)
def test_client_configured_requires_absolute_https(url: str, expected: bool) -> None:
    client = ControlPlaneClient(base_url=url)
    assert client.configured() is expected


def test_client_uses_config_url_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RC_CONTROL_PLANE_URL", "https://crm.example.com")
    assert ControlPlaneClient().configured() is True

    monkeypatch.delenv("RC_CONTROL_PLANE_URL", raising=False)
    assert ControlPlaneClient().configured() is False


def test_endpoint_is_rejected_when_not_configured() -> None:
    client = ControlPlaneClient(base_url="")
    with pytest.raises(ControlPlaneError) as exc:
        client._endpoint("rc/commands/claim")
    assert exc.value.code == "rc_not_configured"


def test_endpoint_builds_generic_path() -> None:
    client = ControlPlaneClient(base_url="https://crm.example.com/api")
    assert client._endpoint("rc/commands/claim") == "https://crm.example.com/api/rc/commands/claim"


def test_auth_header_only_with_credential() -> None:
    anonymous = ControlPlaneClient(base_url="https://crm.example.com")
    assert anonymous._headers() == {}

    credentialed = ControlPlaneClient(
        base_url="https://crm.example.com",
        credential=DeviceCredential(token="harc_x", device_id="d"),
    )
    assert credentialed._headers() == {"Authorization": "Bearer harc_x"}


async def test_claim_pending_is_safe_when_not_configured() -> None:
    # Источник истины — сервер; при недоступности клиент не падает, команд нет.
    client = ControlPlaneClient(base_url="")
    assert await client.claim_pending(device_id="d") == []


async def test_heartbeat_reports_failure_when_not_configured() -> None:
    client = ControlPlaneClient(base_url="")
    assert await client.heartbeat(device_id="d", publication_id="p", state="published_idle") is False


async def test_wakeup_listener_is_optional_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    # python-socketio не установлен в публичной поставке: wakeup тихо отключается,
    # корректность /rc от этого не страдает (команды забирает HTTPS reconcile).
    monkeypatch.setenv("RC_CONTROL_PLANE_URL", "https://crm.example.com")
    listener = WakeupListener()
    started = await listener.start()

    try:
        import socketio  # type: ignore  # noqa: F401

        socketio_available = True
    except ImportError:
        socketio_available = False

    assert started is socketio_available
    await listener.stop()
    assert listener.available is False


async def test_wakeup_listener_off_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RC_CONTROL_PLANE_URL", raising=False)
    listener = WakeupListener(base_url="")
    assert await listener.start() is False
