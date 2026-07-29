"""Узкие тесты браузерного прокси /rc (webapp/api/routes/remote_control.py).

Покрывают обязательные гарантии этапа P7: авторизация только через CRM-сессию
браузера, allowlist маршрутов, отсутствие серверных секретов в ответе, потолок
размера тела и видимый код ошибки для офлайн-устройства. Исходящий транспорт
(``_send_http``) подменяется, чтобы проверять логику прокси без сети.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from core import config, db
from webapp.api import browser_session, server
from webapp.api.routes import remote_control

# Отличительные секреты: по ним проверяем, что серверный токен используется
# только в исходящем запросе и никогда не возвращается браузеру. Значения
# собираются из частей, чтобы не походить на реальный credential.
SERVER_TOKEN = "-".join(("srv", "secret", "token", "abc123"))
SYNC_TOKEN = "-".join(("test", "sync", "token", "xyz"))
CRM_BASE_URL = "https://" + "crm.rc.test" + "/api/v1"
PUBLICATION_ID = "3f2b1a0e-1234-4abc-8def-0123456789ab"
# Владелец серверного токена прокси и «другой участник того же пространства».
OWNER_CRM_USER_ID = 5
OTHER_CRM_USER_ID = 6


def configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "ADMIN_IDS", [100])
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    # Ключ подписи browser_session; без него issue/read отказывают.
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", SYNC_TOKEN)
    monkeypatch.setattr(server, "DEV_SKIP_AUTH", True)
    monkeypatch.setenv("RC_PROXY_CRM_BASE_URL", CRM_BASE_URL)
    monkeypatch.setenv("RC_PROXY_CRM_TOKEN", SERVER_TOKEN)
    # Владелец серверного токена: только его сессия вправе управлять устройством.
    monkeypatch.setenv("RC_PROXY_CRM_OWNER_USER_ID", str(OWNER_CRM_USER_ID))
    db.init()


def crm_cookie(crm_user_id: int = OWNER_CRM_USER_ID) -> dict[str, str]:
    """Действующая CRM-сессия браузера (auth_source=crm)."""
    token = browser_session.issue(crm_user_id=crm_user_id, tenant_id="tenant-rc")
    return {browser_session.COOKIE_NAME: token}


class FakeCrm:
    """Записывает исходящие запросы и отдаёт заготовленный ответ CRM."""

    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self.payload = payload
        self.calls: list[dict] = []

    async def __call__(
        self, method: str, url: str, headers: dict[str, str], json_body: dict | None
    ) -> tuple[int, object]:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "json_body": json_body}
        )
        return self.status, self.payload


async def test_unauthorized_request_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без CRM-сессии браузера прокси отказывает, даже если глобальный auth пройден."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(200, [])
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        # DEV_SKIP_AUTH=True пропускает глобальный middleware, но cookie браузера
        # нет — собственная проверка сессии прокси обязана отказать.
        response = await client.get("/api/rc/publications")
        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}
        assert fake.calls == []  # исходящий запрос к CRM не выполнялся
    finally:
        await client.close()


async def test_route_outside_allowlist_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Действие вне allowlist отклоняется до любого исходящего запроса."""
    configure(tmp_path, monkeypatch)
    with pytest.raises(remote_control.RcProxyError) as excinfo:
        remote_control._resolve_target("drop_all_publications", {})
    assert excinfo.value.code == "forbidden_route"
    assert excinfo.value.status == 403

    # HTTP-уровень: чтение очереди команд браузеру не разрешено (маршрут есть
    # только у раннера). GET на POST-путь прокси отклоняется (404/405).
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.get(
            f"/api/rc/publications/{PUBLICATION_ID}/commands", cookies=crm_cookie()
        )
        assert response.status in (404, 405)
    finally:
        await client.close()


async def test_secrets_never_reach_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Серверный CRM-токен уходит только в исходящий запрос, не в ответ."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(200, [{"id": PUBLICATION_ID, "state": "published_idle"}])
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.get("/api/rc/publications", cookies=crm_cookie())
        assert response.status == 200
        body_text = await response.text()

        # Серверный токен использован в исходящем заголовке и отсутствует в ответе.
        assert len(fake.calls) == 1
        sent_headers = fake.calls[0]["headers"]
        assert sent_headers["Authorization"] == f"Bearer {SERVER_TOKEN}"
        assert SERVER_TOKEN not in body_text
        # Sync-токен (ключ подписи сессии) тоже не должен попасть в ответ.
        assert SYNC_TOKEN not in body_text
        # Прокси не пересылает произвольные заголовки браузера: только свой набор.
        assert set(sent_headers) == {"Authorization", "Content-Type", "Accept"}
    finally:
        await client.close()


async def test_oversized_body_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тело команды сверх потолка отклоняется без исходящего запроса."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        huge_text = "x" * (remote_control.MAX_BODY_BYTES + 1024)
        body = json.dumps({"commandType": "prompt", "payload": {"text": huge_text}})
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            data=body,
            headers={"Content-Type": "application/json"},
            cookies=crm_cookie(),
        )
        assert response.status == 413
        assert await response.json() == {"error": "body_too_large"}
        assert fake.calls == []
    finally:
        await client.close()


async def test_long_valid_body_is_not_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Длинный, но допустимый промпт доезжает целиком.

    StreamReader.read(n) отдаёт лишь то, что уже в буфере: одного чтения на
    такое тело не хватает, и раньше валидный запрос отбивался как invalid_json.
    Повторяем отправку, потому что дефект проявлялся не на каждой попытке.
    """
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {"command": {"id": "cmd-1"}, "created": True})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        # Длинный, но допустимый промпт: под потолком символов и под потолком тела.
        prompt = "prompt-line " * 600
        body = json.dumps({"commandType": "prompt", "payload": {"prompt": prompt}})
        assert len(prompt) <= remote_control.MAX_PROMPT_CHARS
        assert len(body.encode()) < remote_control.MAX_BODY_BYTES
        for _ in range(3):
            response = await client.post(
                f"/api/rc/publications/{PUBLICATION_ID}/commands",
                data=body,
                headers={"Content-Type": "application/json"},
                cookies=crm_cookie(),
            )
            assert response.status == 201, await response.json()
        assert len(fake.calls) == 3
        assert fake.calls[0]["json_body"]["payload"]["prompt"] == prompt
    finally:
        await client.close()


async def test_offline_device_returns_server_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Офлайн-устройство даёт видимый серверный код, а не ложный успех."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(409, {"errorCode": "DEVICE_OFFLINE", "message": "device is offline"})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            json={"commandType": "prompt", "payload": {"prompt": "привет"}},
            cookies=crm_cookie(),
        )
        assert response.status == 409
        assert await response.json() == {"error": "DEVICE_OFFLINE"}
    finally:
        await client.close()


async def test_create_command_forwards_validated_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Счастливая дорога: валидное тело уходит в CRM, ответ 201."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {"id": "cmd-1", "commandType": "prompt", "status": "pending"})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            json={"commandType": "prompt", "payload": {"prompt": "привет"}},
            cookies=crm_cookie(),
        )
        assert response.status == 201
        assert (await response.json())["id"] == "cmd-1"
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{CRM_BASE_URL}/cli-agent/remote-publications/{PUBLICATION_ID}/commands"
        assert call["json_body"] == {"commandType": "prompt", "payload": {"prompt": "привет"}}
    finally:
        await client.close()


async def test_prompt_payload_with_foreign_key_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Промпт под чужим ключом — отказ, а не тихий запуск агента с пустым текстом.

    Раннер читает payload['prompt'] (chat_remote_control._ingest_prompt_command).
    Исторический ключ 'text' доезжал до устройства пустой строкой: агент
    стартовал, ничего не делал и отчитывался успехом.
    """
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        for payload in ({"text": "привет"}, {}, {"prompt": "   "}, {"prompt": 42}):
            response = await client.post(
                f"/api/rc/publications/{PUBLICATION_ID}/commands",
                json={"commandType": "prompt", "payload": payload},
                cookies=crm_cookie(),
            )
            assert response.status == 400, payload
            assert await response.json() == {"error": "invalid_command"}
        # Ни одна из попыток не дошла до CRM.
        assert fake.calls == []
    finally:
        await client.close()


async def test_prompt_payload_extra_keys_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Наружу уходит только текст промпта: лишние ключи браузера не пересылаются."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {"command": {"id": "cmd-1"}, "created": True})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            json={
                "commandType": "prompt",
                "payload": {"prompt": "привет", "cwd": "/Users/owner/secret", "text": "мусор"},
            },
            cookies=crm_cookie(),
        )
        assert response.status == 201
        assert fake.calls[0]["json_body"] == {
            "commandType": "prompt",
            "payload": {"prompt": "привет"},
        }
    finally:
        await client.close()


async def test_prompt_longer_than_backend_limit_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Промпт длиннее потолка бэкенда отбивается своим кодом, а не общим 400."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            json={
                "commandType": "prompt",
                "payload": {"prompt": "я" * (remote_control.MAX_PROMPT_CHARS + 1)},
            },
            cookies=crm_cookie(),
        )
        assert response.status == 400
        assert await response.json() == {"error": "prompt_too_long"}
        assert fake.calls == []
    finally:
        await client.close()


async def test_idempotency_key_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключ идемпотентности браузера доезжает до CRM — иначе повтор задваивает промпт."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {"id": "cmd-1", "created": False})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            json={"commandType": "prompt", "payload": {"prompt": "привет"}},
            headers={"Idempotency-Key": "web_11112222-3333-4444-5555-666677778888"},
            cookies=crm_cookie(),
        )
        assert response.status == 201
        sent_headers = fake.calls[0]["headers"]
        assert sent_headers["Idempotency-Key"] == "web_11112222-3333-4444-5555-666677778888"
        # Серверный токен по-прежнему не подменяется и не дублируется браузером.
        assert sent_headers["Authorization"] == f"Bearer {SERVER_TOKEN}"
        assert set(sent_headers) == {
            "Authorization",
            "Content-Type",
            "Accept",
            "Idempotency-Key",
        }
    finally:
        await client.close()


async def test_request_without_idempotency_key_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без заголовка исходящий набор заголовков остаётся прежним."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {"id": "cmd-1"})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            json={"commandType": "stop"},
            cookies=crm_cookie(),
        )
        assert response.status == 201
        assert set(fake.calls[0]["headers"]) == {"Authorization", "Content-Type", "Accept"}
    finally:
        await client.close()


async def test_malformed_idempotency_key_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кривой ключ — явный отказ, а не тихая отправка без него (иначе будет дубль)."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            json={"commandType": "prompt", "payload": {"prompt": "привет"}},
            headers={"Idempotency-Key": "short"},
            cookies=crm_cookie(),
        )
        assert response.status == 400
        assert await response.json() == {"error": "invalid_idempotency_key"}
        assert fake.calls == []
    finally:
        await client.close()


async def test_invalid_publication_id_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не-UUID в пути отклоняется до исходящего запроса (защита от path-injection)."""
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(200, {})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.delete(
            "/api/rc/publications/..%2Fescape", cookies=crm_cookie()
        )
        assert response.status == 400
        assert fake.calls == []
    finally:
        await client.close()


class ChunkedContent:
    """StreamReader, который отдаёт тело кусками — как настоящая сеть."""

    def __init__(self, payload: bytes, chunk: int) -> None:
        self._rest = payload
        self._chunk = chunk

    async def read(self, n: int = -1) -> bytes:
        if not self._rest:
            return b""
        size = self._chunk if n < 0 else min(n, self._chunk)
        piece, self._rest = self._rest[:size], self._rest[size:]
        return piece


class FakeRequest:
    def __init__(self, payload: bytes, chunk: int, *, declared: int | None = None) -> None:
        self.content = ChunkedContent(payload, chunk)
        self.content_length = declared


async def test_body_is_read_until_eof_not_one_buffer() -> None:
    """Тело собирается целиком, даже когда каждый read отдаёт по кусочку.

    Это и был дефект: одиночный read(MAX+1) возвращает только то, что уже в
    буфере, и валидный промпт молча превращался в обрезанный JSON (400).
    """
    payload = json.dumps({"commandType": "prompt", "payload": {"prompt": "a" * 40_000}}).encode()
    request = FakeRequest(payload, 4096, declared=len(payload))

    raw = await remote_control._read_body_bytes(request)  # type: ignore[arg-type]

    assert raw == payload


async def test_oversized_stream_is_413_even_without_content_length() -> None:
    """Поток сверх потолка — это 413, а не «сломанный JSON»."""
    payload = b"x" * (remote_control.MAX_BODY_BYTES + 2048)
    request = FakeRequest(payload, 4096)

    with pytest.raises(remote_control.RcProxyError) as error:
        await remote_control._read_body_bytes(request)  # type: ignore[arg-type]

    assert error.value.status == 413
    assert error.value.code == "body_too_large"


async def test_session_of_other_participant_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Чужая CRM-сессия не управляет устройством владельца (403 до запроса к CRM).

    Сессию с ``auth_source='crm'`` получает ЛЮБОЙ участник пространства, обменявший
    свой ``hat_``-тикет, а исходящий запрос уходит с общим серверным токеном, то есть
    от имени владельца. Без сверки владельца это был бы удалённый запуск кода на его
    компьютере от лица постороннего.
    """
    configure(tmp_path, monkeypatch)
    fake = FakeCrm(201, {"id": "cmd-1"})
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            f"/api/rc/publications/{PUBLICATION_ID}/commands",
            data=json.dumps({"commandType": "prompt", "payload": {"prompt": "rm -rf"}}),
            headers={"Content-Type": "application/json"},
            cookies=crm_cookie(OTHER_CRM_USER_ID),
        )
        assert response.status == 403
        assert await response.json() == {"error": "not_owner"}
        # Ни одного исходящего запроса: отказ до обращения к CRM.
        assert fake.calls == []

        # Чтение списка публикаций владельца — тоже не для чужой сессии.
        listing = await client.get(
            "/api/rc/publications", cookies=crm_cookie(OTHER_CRM_USER_ID)
        )
        assert listing.status == 403
        assert fake.calls == []
    finally:
        await client.close()


async def test_owner_not_configured_disables_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без RC_PROXY_CRM_OWNER_USER_ID прокси выключен (default deny), а не «пускаем всех»."""
    configure(tmp_path, monkeypatch)
    monkeypatch.delenv("RC_PROXY_CRM_OWNER_USER_ID", raising=False)
    fake = FakeCrm(200, [])
    monkeypatch.setattr(remote_control, "_send_http", fake)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.get("/api/rc/publications", cookies=crm_cookie())
        assert response.status == 503
        assert await response.json() == {"error": "rc_not_configured"}
        assert fake.calls == []
    finally:
        await client.close()
