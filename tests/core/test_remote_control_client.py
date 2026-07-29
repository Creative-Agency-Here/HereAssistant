"""Клиент control-plane /rc: пустой URL выключает режим, HTTPS — источник истины.

Сеть в тестах не используется: aiohttp-сессия подменяется заглушкой, которая
протоколирует обращения. Пустой базовый URL означает выключенный режим — клиент
не делает ни одного сетевого вызова. WS трактуются только как уведомление: при
недоступном WS команды всё равно забираются обычным HTTPS-опросом.
"""

from __future__ import annotations

from typing import Any

from core.remote_control.control_plane_client import ControlPlaneClient, WakeupListener


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
    """Замена aiohttp.ClientSession: сеть не трогает, пишет факт обращения."""

    def __init__(self, payload: Any = None, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.posts: list[tuple[str, Any]] = []

    def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        self.posts.append((url, json))
        return _FakeResponse(self.status, self.payload)


async def test_empty_base_url_disables_mode_without_any_network_call() -> None:
    session = _FakeSession()
    client = ControlPlaneClient(base_url="", session=session)
    assert client.configured() is False

    # Все операции безопасны и завершаются «нет данных / не доставлено».
    assert await client.claim_pending(device_id="d") == []
    assert await client.post_result({"eventId": "e"}) is False
    assert await client.heartbeat(device_id="d", publication_id="p", state="published_idle") is False

    # Режим выключен — ни одного обращения к транспортному слою.
    assert session.posts == []


async def test_commands_fetched_via_https_when_ws_unavailable() -> None:
    # WS-wakeup недоступен (нет URL) — это не фатально.
    listener = WakeupListener(base_url="")
    assert await listener.start() is False

    # Источник истины — HTTPS claim: даже без WS команды забираются опросом.
    session = _FakeSession(payload={"commands": [{"commandId": "c1", "sequence": 1}]})
    client = ControlPlaneClient(base_url="https://crm.example.com", session=session)
    commands = await client.claim_pending(device_id="device-1", last_sequence=0)

    assert commands == [{"commandId": "c1", "sequence": 1}]
    # Ровно один HTTPS-вызов к источнику истины.
    assert len(session.posts) == 1
    url, body = session.posts[0]
    assert url == "https://crm.example.com/rc/commands/claim"
    assert body == {"deviceId": "device-1", "lastSequence": 0}


async def test_claim_pending_tolerates_server_error() -> None:
    # Ошибка сервера не роняет клиента: команд просто нет.
    session = _FakeSession(payload=None, status=500)
    client = ControlPlaneClient(base_url="https://crm.example.com", session=session)
    assert await client.claim_pending(device_id="d") == []


async def test_claim_pending_ignores_malformed_payload() -> None:
    # Сервер ответил, но без списка команд — считаем, что команд нет.
    session = _FakeSession(payload={"unexpected": True}, status=200)
    client = ControlPlaneClient(base_url="https://crm.example.com", session=session)
    assert await client.claim_pending(device_id="d") == []


async def test_post_result_confirms_only_on_success() -> None:
    ok = _FakeSession(payload={"ok": True}, status=200)
    assert await ControlPlaneClient(base_url="https://x.example", session=ok).post_result({"e": 1})

    unauthorized = _FakeSession(payload=None, status=401)
    client = ControlPlaneClient(base_url="https://x.example", session=unauthorized)
    assert await client.post_result({"e": 1}) is False
