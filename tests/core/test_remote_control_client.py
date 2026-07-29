"""Клиент control-plane /rc: пустой URL выключает режим, HTTPS — источник истины.

Сеть в тестах не используется: aiohttp-сессия подменяется заглушкой, которая
протоколирует обращения. Пустой базовый URL означает выключенный режим — клиент
не делает ни одного сетевого вызова. WS трактуются только как уведомление: при
недоступном WS команды всё равно забираются обычным HTTPS-опросом.

Реальный контракт сервера — ``cli-agent/runner`` (Admin Panel,
``remote-control.runner.controller.ts``): маршруты вида
``cli-agent/runner/publications/:id/commands`` (список, GET) и
``cli-agent/runner/publications/:id/commands/:commandId/claim`` (поштучный
claim, POST) — НЕ выдуманные ``rc/commands/claim``/``rc/events``/``rc/heartbeat``,
которые раньше давали 404 на реальном сервере. ``test_client_uses_real_runner_paths``
специально ловит регресс на старые пути.
"""

from __future__ import annotations

from typing import Any

from core.remote_control.control_plane_client import ControlPlaneClient, WakeupListener
from core.remote_control.credential_store import DeviceCredential

_EXCHANGE_RESPONSE = {"accessToken": "tok", "expiresAt": "2099-01-01T00:00:00.000Z"}


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

    Первый POST (exchange) всегда получает ``exchange_payload``; последующие
    обращения — заданный ``payload``/``status``. Так тест конкретной операции не
    обязан вручную симулировать обмен токеном на каждом вызове.
    """

    def __init__(self, payload: Any = None, status: int = 200, exchange_payload: Any = None) -> None:
        self.payload = payload
        self.status = status
        self.exchange_payload = exchange_payload or _EXCHANGE_RESPONSE
        self.posts: list[tuple[str, Any]] = []
        self.gets: list[tuple[str, Any]] = []
        self.methods: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        json: Any = None,
        params: Any = None,
        headers: Any = None,
    ) -> _FakeResponse:
        """Клиент зовёт сессию объявленным методом — заглушка это фиксирует."""
        self.methods.append((method.upper(), url))
        if method.upper() == "GET":
            self.gets.append((url, params))
            return _FakeResponse(self.status, self.payload)
        self.posts.append((url, json))
        if url.endswith("/cli-agent/runner/exchange"):
            return _FakeResponse(200, self.exchange_payload)
        return _FakeResponse(self.status, self.payload)

    def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        return self.request("POST", url, json=json, headers=headers)

    def get(self, url: str, params: Any = None, headers: Any = None) -> _FakeResponse:
        return self.request("GET", url, params=params, headers=headers)


def _client(session: _FakeSession, *, credential: bool = True) -> ControlPlaneClient:
    return ControlPlaneClient(
        base_url="https://crm.example.com",
        credential=DeviceCredential(token="harc_x", device_id="d") if credential else None,
        session=session,
    )


async def test_empty_base_url_disables_mode_without_any_network_call() -> None:
    session = _FakeSession()
    client = ControlPlaneClient(base_url="", session=session)
    assert client.configured() is False

    # Все операции безопасны и завершаются «нет данных / не доставлено».
    assert await client.list_commands(publication_id="pub-1") == []
    assert await client.claim_command(
        publication_id="pub-1", command_id="cmd-1", runner_epoch=1, lease_owner="d"
    ) is None
    assert await client.submit_command_result(
        publication_id="pub-1", command_id="cmd-1", status="succeeded"
    ) is False
    assert await client.send_events(publication_id="pub-1", events=[{"eventId": "e"}]) is False
    assert await client.heartbeat(publication_id="pub-1", state="published_idle") is False

    # Режим выключен — ни одного обращения к транспортному слою.
    assert session.posts == []
    assert session.gets == []


async def test_list_commands_hits_publications_commands_path() -> None:
    """Регрессия: GET к реальному ``cli-agent/runner/publications/:id/commands``.

    Если кто-то снова подставит выдуманный ``rc/commands/claim`` (или любой
    путь без префикса ``cli-agent/runner/publications/:id/``), эта проверка
    обязана покраснеть.
    """
    session = _FakeSession(payload=[{"id": "c1", "sequence": 1, "commandType": "prompt"}])
    client = _client(session)
    commands = await client.list_commands(publication_id="pub-1", after_sequence=5)

    assert commands == [{"id": "c1", "sequence": 1, "commandType": "prompt"}]
    # Один exchange + один GET к источнику истины.
    assert len(session.posts) == 1
    assert len(session.gets) == 1
    url, params = session.gets[0]
    assert url == "https://crm.example.com/cli-agent/runner/publications/pub-1/commands"
    assert "rc/commands/claim" not in url
    assert params == {"afterSequence": "5"}


async def test_list_commands_tolerates_server_error() -> None:
    session = _FakeSession(payload=None, status=500)
    client = _client(session)
    assert await client.list_commands(publication_id="pub-1") == []


async def test_list_commands_ignores_malformed_payload() -> None:
    session = _FakeSession(payload={"unexpected": True}, status=200)
    client = _client(session)
    assert await client.list_commands(publication_id="pub-1") == []


async def test_claim_command_hits_per_command_claim_path() -> None:
    """Регрессия: поштучный claim — путь с ``commandId``, а не общий девайс-claim.

    Старый выдуманный маршрут был один на устройство (``rc/commands/claim``,
    без commandId в пути); реальный сервер claim'ит команды по одной. Эта
    проверка обязана покраснеть, если путь снова свернут к старой форме.
    """
    session = _FakeSession(payload={"id": "cmd-1", "status": "claimed"})
    client = _client(session)
    result = await client.claim_command(
        publication_id="pub-1",
        command_id="cmd-1",
        runner_epoch=3,
        lease_owner="dev-1",
        lease_ttl_ms=15000,
    )

    assert result == {"id": "cmd-1", "status": "claimed"}
    assert len(session.posts) == 2  # exchange + claim
    url, body = session.posts[-1]
    assert url == "https://crm.example.com/cli-agent/runner/publications/pub-1/commands/cmd-1/claim"
    assert "rc/commands/claim" not in url
    assert body == {"runnerEpoch": 3, "leaseOwner": "dev-1", "leaseTtlMs": 15000}


async def test_claim_command_returns_none_on_failure() -> None:
    session = _FakeSession(payload=None, status=409)
    client = _client(session)
    result = await client.claim_command(
        publication_id="pub-1", command_id="cmd-1", runner_epoch=1, lease_owner="dev-1"
    )
    assert result is None


async def test_create_publication_sends_json_body_and_returns_server_id() -> None:
    """Публикация уходит телом JSON, а не как угодно иначе.

    Регрессия: тело передавалось позиционно-именованным ``payload=``, которого у
    транспорта нет — вызов падал TypeError ДО сети, и публикация не создавалась
    вовсе. Никакой ошибки в логе при этом не было видно.
    """
    session = _FakeSession(payload={"id": "srv-pub-1"})
    client = _client(session)
    conversation = "33333333-3333-4333-8333-333333333333"
    remote_id = await client.create_publication(
        public_id="chat:1:2",
        privacy_mode="crm",
        capabilities={"remotePrompt": True, "stop": True},
        ttl_minutes=30,
        conversation_id=conversation,
    )

    assert remote_id == "srv-pub-1"
    method, url = session.methods[-1]
    assert method == "POST"
    assert url == "https://crm.example.com/cli-agent/runner/publications"
    assert session.posts[-1][1] == {
        "publicId": "chat:1:2",
        "privacyMode": "crm",
        "capabilities": {"remotePrompt": True, "stop": True},
        "ttlMinutes": 30,
        "conversationId": conversation,
    }


async def test_create_publication_drops_non_uuid_conversation_id() -> None:
    # Сервер валидирует conversationId через @IsUUID: кривое значение отклонило
    # бы публикацию ЦЕЛИКОМ, поэтому не-UUID не уходит вовсе.
    session = _FakeSession(payload={"id": "srv-pub-2"})
    client = _client(session)
    await client.create_publication(
        public_id="chat:1:2", privacy_mode="crm", conversation_id="chat:1:2"
    )
    assert "conversationId" not in session.posts[-1][1]


async def test_close_publication_uses_delete_method() -> None:
    """Снятие публикации — именно DELETE.

    Регрессия: транспорт выбирал «GET или POST», и DELETE уходил POST-ом на
    ``publications/:id`` — публикация молча оставалась живой до истечения TTL.
    """
    session = _FakeSession(payload={"ok": True})
    client = _client(session)
    assert await client.close_publication(publication_id="pub-1") is True

    method, url = session.methods[-1]
    assert method == "DELETE"
    assert url == "https://crm.example.com/cli-agent/runner/publications/pub-1"


async def test_submit_command_result_hits_per_command_result_path() -> None:
    session = _FakeSession(payload={"id": "cmd-1", "status": "succeeded"})
    client = _client(session)
    ok = await client.submit_command_result(
        publication_id="pub-1",
        command_id="cmd-1",
        status="succeeded",
        result_summary={"filesChanged": 2},
    )

    assert ok is True
    method, url = session.methods[-1]
    # Результат адресуется КОНКРЕТНОЙ команде: общего девайс-маршрута нет.
    assert method == "POST"
    assert url == "https://crm.example.com/cli-agent/runner/publications/pub-1/commands/cmd-1/result"
    assert session.posts[-1][1] == {
        "status": "succeeded",
        "resultSummary": {"filesChanged": 2},
    }


async def test_submit_command_result_carries_error_code() -> None:
    # Код причины — единственный способ отличить приватный отказ от сбоя
    # провайдера: статус у них один и тот же (failed).
    session = _FakeSession(payload={"id": "cmd-1", "status": "failed"})
    client = _client(session)
    await client.submit_command_result(
        publication_id="pub-1",
        command_id="cmd-1",
        status="failed",
        error_code="PRIVACY_DENIED",
    )
    assert session.posts[-1][1] == {"status": "failed", "errorCode": "PRIVACY_DENIED"}


async def test_send_events_hits_publication_events_path_as_batch() -> None:
    session = _FakeSession(payload={"acceptedCount": 1, "duplicateEventIds": []})
    client = _client(session)
    events = [{"eventId": "e1", "type": "rc.command_status", "payload": {"state": "succeeded"}}]
    ok = await client.send_events(publication_id="pub-1", events=events)

    assert ok is True
    url, body = session.posts[-1]
    assert url == "https://crm.example.com/cli-agent/runner/publications/pub-1/events"
    assert body == {"events": events}


async def test_heartbeat_hits_publication_heartbeat_path() -> None:
    session = _FakeSession(payload={"state": "published_idle"})
    client = _client(session)
    ok = await client.heartbeat(publication_id="pub-1", state="published_idle")

    assert ok is True
    url, body = session.posts[-1]
    assert url == "https://crm.example.com/cli-agent/runner/publications/pub-1/heartbeat"
    assert body == {"state": "published_idle"}


async def test_heartbeat_carries_late_conversation_binding() -> None:
    # Поздняя привязка публикации к сессии CRM: диалог появляется только после
    # первого синка, поэтому доезжает heartbeat'ом, а не второй публикацией.
    session = _FakeSession(payload={"state": "published_idle"})
    client = _client(session)
    conversation = "44444444-4444-4444-8444-444444444444"
    await client.heartbeat(
        publication_id="pub-1", state="running", conversation_id=conversation
    )
    assert session.posts[-1][1] == {"state": "running", "conversationId": conversation}

    # Не-UUID сервер отклонил бы (@IsUUID) — такое значение не отправляется.
    await client.heartbeat(
        publication_id="pub-1", state="running", conversation_id="chat:1:2"
    )
    assert session.posts[-1][1] == {"state": "running"}


async def test_commands_fetched_via_https_when_ws_unavailable() -> None:
    # WS-wakeup недоступен (нет URL) — это не фатально.
    listener = WakeupListener(base_url="")
    assert await listener.start() is False

    # Источник истины — HTTPS список команд: даже без WS команды забираются опросом.
    session = _FakeSession(payload=[{"id": "c1", "sequence": 1}])
    client = _client(session)
    commands = await client.list_commands(publication_id="pub-1")

    assert commands == [{"id": "c1", "sequence": 1}]
