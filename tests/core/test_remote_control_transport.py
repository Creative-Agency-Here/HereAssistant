"""Транспорт /rc: default-deny конфигурация, HTTPS как источник истины, WS-wakeup опционален."""

from __future__ import annotations

from typing import Any

import pytest

from core.remote_control import config
from core.remote_control.control_plane_client import (
    ControlPlaneClient,
    ControlPlaneError,
    WakeupListener,
)
from core.remote_control.credential_store import DeviceCredential


class _FakeResponse:
    """Ответ-заглушка: поддерживает async with и отдаёт заранее заданный JSON."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSession:
    """Замена aiohttp.ClientSession: сеть не трогает, пишет факт обращения.

    ``responses`` — очередь ответов по мере обращений (exchange первым, затем
    основной вызов); последний элемент переиспользуется, если очередь короче
    числа обращений.
    """

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, Any, Any]] = []  # (method, url, json, headers)

    def _next(self) -> tuple[int, Any]:
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        self.calls.append(("POST", url, json, headers))
        status, payload = self._next()
        return _FakeResponse(status, payload)

    def get(self, url: str, params: Any = None, headers: Any = None) -> _FakeResponse:
        self.calls.append(("GET", url, params, headers))
        status, payload = self._next()
        return _FakeResponse(status, payload)


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
        client._endpoint("cli-agent/runner/exchange")
    assert exc.value.code == "rc_not_configured"


def test_endpoint_builds_generic_path() -> None:
    client = ControlPlaneClient(base_url="https://crm.example.com/api")
    assert (
        client._endpoint("cli-agent/runner/exchange")
        == "https://crm.example.com/api/cli-agent/runner/exchange"
    )


async def test_no_auth_header_without_credential() -> None:
    # Без device credential обмену не с чем идти — Authorization не появляется.
    session = _FakeSession([(200, [])])
    client = ControlPlaneClient(base_url="https://crm.example.com", session=session)
    await client.list_commands(publication_id="pub-1")

    assert len(session.calls) == 1
    _method, _url, _params, headers = session.calls[0]
    assert headers == {}


async def test_authed_call_exchanges_credential_first_and_sends_access_token() -> None:
    # Raw credential НИКОГДА не уходит как Bearer — только в теле /exchange;
    # сам защищённый вызов несёт короткий access-токен, выданный сервером.
    session = _FakeSession(
        [
            (200, {"accessToken": "short-lived-token", "expiresAt": "2099-01-01T00:00:00.000Z"}),
            (200, []),
        ]
    )
    client = ControlPlaneClient(
        base_url="https://crm.example.com",
        credential=DeviceCredential(token="harc_raw_secret", device_id="d"),
        session=session,
    )
    await client.list_commands(publication_id="pub-1")

    assert len(session.calls) == 2
    exchange_method, exchange_url, exchange_body, exchange_headers = session.calls[0]
    assert exchange_method == "POST"
    assert exchange_url == "https://crm.example.com/cli-agent/runner/exchange"
    assert exchange_body == {"credential": "harc_raw_secret"}
    assert exchange_headers == {}  # raw credential никогда не уходит как Bearer

    call_method, call_url, _params, call_headers = session.calls[1]
    assert call_method == "GET"
    assert call_url == "https://crm.example.com/cli-agent/runner/publications/pub-1/commands"
    assert call_headers == {"Authorization": "Bearer short-lived-token"}


async def test_access_token_is_refreshed_once_on_401() -> None:
    # Токен протух между проверкой срока и запросом (или сервер отозвал его
    # досрочно) — клиент обновляет его через exchange и повторяет ровно один раз,
    # не роняя цикл reconcile/heartbeat.
    session = _FakeSession(
        [
            (200, {"accessToken": "stale-token", "expiresAt": "2099-01-01T00:00:00.000Z"}),
            (401, None),
            (200, {"accessToken": "fresh-token", "expiresAt": "2099-01-01T00:00:00.000Z"}),
            (200, []),
        ]
    )
    client = ControlPlaneClient(
        base_url="https://crm.example.com",
        credential=DeviceCredential(token="harc_raw_secret", device_id="d"),
        session=session,
    )
    result = await client.list_commands(publication_id="pub-1")

    assert result == []
    # exchange → 401 → повторный exchange → успешный вызов = 4 обращения.
    assert len(session.calls) == 4
    last_call = session.calls[-1]
    assert last_call[3] == {"Authorization": "Bearer fresh-token"}


async def test_list_commands_is_safe_when_not_configured() -> None:
    # Источник истины — сервер; при недоступности клиент не падает, команд нет.
    client = ControlPlaneClient(base_url="")
    assert await client.list_commands(publication_id="pub-1") == []


async def test_claim_command_returns_none_on_conflict() -> None:
    # CAS-проигрыш (409) — штатный исход поштучного claim, не повод падать.
    session = _FakeSession(
        [
            (200, {"accessToken": "tok", "expiresAt": "2099-01-01T00:00:00.000Z"}),
            (409, None),
        ]
    )
    client = ControlPlaneClient(
        base_url="https://crm.example.com",
        credential=DeviceCredential(token="harc_x", device_id="d"),
        session=session,
    )
    result = await client.claim_command(
        publication_id="pub-1", command_id="cmd-1", runner_epoch=1, lease_owner="dev-1"
    )
    assert result is None


async def test_heartbeat_reports_failure_when_not_configured() -> None:
    client = ControlPlaneClient(base_url="")
    assert await client.heartbeat(publication_id="p", state="published_idle") is False


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
