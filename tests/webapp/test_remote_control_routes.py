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
    db.init()


def crm_cookie() -> dict[str, str]:
    """Действующая CRM-сессия браузера (auth_source=crm)."""
    token = browser_session.issue(crm_user_id=5, tenant_id="tenant-rc")
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
            json={"commandType": "prompt", "payload": {"text": "привет"}},
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
            json={"commandType": "prompt", "payload": {"text": "привет"}},
            cookies=crm_cookie(),
        )
        assert response.status == 201
        assert (await response.json())["id"] == "cmd-1"
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{CRM_BASE_URL}/cli-agent/remote-publications/{PUBLICATION_ID}/commands"
        assert call["json_body"] == {"commandType": "prompt", "payload": {"text": "привет"}}
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
