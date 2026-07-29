"""Браузерный прокси WebApp к HereCRM для режима /rc (этап P7).

Браузер никогда не говорит с HereCRM напрямую и не получает device/sync
credential: он ходит только в этот прокси под существующей HttpOnly-сессией
браузера (``browser_session.py``). Сервер хранит CRM-токен в окружении процесса
и подставляет его в исходящие запросы сам; в ответ браузеру токен не попадает.

Разрешены только три пользовательских маршрута control-plane (источник
контракта — ``remote-control.controller.ts`` бэкенда HereCRM):

    GET    /cli-agent/remote-publications                 список моих публикаций
    POST   /cli-agent/remote-publications/:id/commands     постановка команды
    DELETE /cli-agent/remote-publications/:id              снять публикацию

Всё, что не входит в allowlist маршрутов/методов, отклоняется до любого
исходящего запроса. Произвольные заголовки браузера не пересылаются.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from webapp.api import browser_session

# Дубликат RC_DURABLE_COMMAND_TYPES бэкенда: команды, которые браузер вправе
# поставить. approval_decision/git_preflight — служебные сигналы раннера, из
# браузера не создаются, поэтому намеренно отсутствуют.
RC_DURABLE_COMMAND_TYPES = ("prompt", "stop", "git_commit", "git_push")

# Жёсткий потолок тела команды. Промпт — текст, а не файл; большего не нужно,
# а ограничение защищает прокси от чтения произвольно больших тел.
MAX_BODY_BYTES = 64 * 1024

# Единственные маршруты control-plane, которые прокси когда-либо вызывает.
# Ключ — внутреннее имя действия (его передаёт зарегистрированный handler),
# значение — HTTP-метод и шаблон пути HereCRM. Публикация адресуется UUID
# (бэкенд валидирует ParseUUIDPipe), поэтому в шаблоне один плейсхолдер.
RC_ALLOWLIST: dict[str, tuple[str, str]] = {
    "list_publications": ("GET", "/cli-agent/remote-publications"),
    "create_command": ("POST", "/cli-agent/remote-publications/{publication_id}/commands"),
    "close_publication": ("DELETE", "/cli-agent/remote-publications/{publication_id}"),
}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Код ошибки, который бэкенд кладёт в тело ответа. Имя повторяет контракт
# бэкенда (errorCode в RunnerCommandResultDto и ошибках сервиса).
_KNOWN_ERROR_CODES = ("DEVICE_OFFLINE", "PUBLICATION_EXPIRED", "PRIVACY_DENIED", "CAPABILITY_UNAVAILABLE")


class RcProxyError(RuntimeError):
    """Безопасная ошибка прокси: код + HTTP-статус, без тела ответа HereCRM."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def _crm_base_url() -> str:
    """Базовый https URL CRM API. Пусто/не-https = прокси выключен (default-deny)."""
    base = os.environ.get("RC_PROXY_CRM_BASE_URL", "").strip().rstrip("/")
    parsed = urlparse(base)
    if not base or parsed.scheme != "https" or not parsed.netloc:
        raise RcProxyError("rc_not_configured", 503)
    return base


def _crm_token() -> str:
    """Серверный CRM-токен. Читается только из окружения процесса."""
    token = os.environ.get("RC_PROXY_CRM_TOKEN", "").strip()
    if not token:
        raise RcProxyError("rc_not_configured", 503)
    return token


def _require_browser_session(request: web.Request) -> dict:
    """Пропускает только действующую CRM-сессию браузера.

    Глобальный auth_middleware может пустить запрос по Telegram initData или
    dev-skip, но управление чужой сессией /rc требует именно входа через SSO
    HereCRM: только тогда browser_session несёт crm_user_id и tenant_id.
    """
    token = request.cookies.get(browser_session.COOKIE_NAME, "")
    session = browser_session.read(token)
    if not session or session.get("auth_source") != "crm":
        raise RcProxyError("unauthorized", 401)
    return session


def _validate_publication_id(raw: str) -> str:
    """Строгий UUID: защищает путь CRM от path-injection через match_info."""
    if not isinstance(raw, str) or not _UUID_RE.match(raw):
        raise RcProxyError("invalid_publication_id", 400)
    return raw


def _resolve_target(action: str, params: dict[str, str]) -> tuple[str, str]:
    """Разрешает действие в метод + путь CRM только из allowlist.

    Это защита в глубину: зарегистрированные маршруты и так соответствуют
    ключам, но любая будущая опечатка/чужой ключ упирается в отказ, а не в
    исходящий запрос куда попало.
    """
    entry = RC_ALLOWLIST.get(action)
    if entry is None:
        raise RcProxyError("forbidden_route", 403)
    method, template = entry
    path = template
    for key, value in params.items():
        path = path.replace("{" + key + "}", value)
    return method, path


async def _read_command_body(request: web.Request) -> dict:
    """Читает тело команды с потолком размера и валидацией типа команды."""
    raw = await request.content.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        raise RcProxyError("body_too_large", 413)
    if not raw:
        raise RcProxyError("invalid_json", 400)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise RcProxyError("invalid_json", 400) from error
    if not isinstance(body, dict):
        raise RcProxyError("invalid_json", 400)
    command_type = body.get("commandType")
    if command_type not in RC_DURABLE_COMMAND_TYPES:
        raise RcProxyError("invalid_command", 400)
    payload = body.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise RcProxyError("invalid_command", 400)
    command: dict = {"commandType": command_type}
    if payload is not None:
        command["payload"] = payload
    return command


def _error_code(status: int, payload: object) -> str:
    """Достаёт безопасный код ошибки из ответа CRM, не копируя всё тело.

    Офлайн-устройство обязано дать видимый серверный код (DEVICE_OFFLINE), а не
    ложный успех: код приходит в поле errorCode. Если его нет — маппинг по
    статусу, чтобы браузер всегда получал машиночитаемую причину.
    """
    if isinstance(payload, dict):
        for key in ("errorCode", "code"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip() in _KNOWN_ERROR_CODES:
            return message.strip()
    if status in (401, 403):
        return "rc_forbidden"
    if status == 404:
        return "rc_not_found"
    if status == 409:
        return "rc_conflict"
    return "crm_unavailable"


async def _send_http(
    method: str, url: str, headers: dict[str, str], json_body: dict | None
) -> tuple[int, object]:
    """Транспортный слой: один исходящий запрос к CRM. Тонкая шейв-функция.

    Заголовки собирает только прокси (Authorization с серверным токеном +
    Content-Type); заголовки браузера сюда не проходят.
    """
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=headers, json=json_body) as response:
                try:
                    payload: object = await response.json()
                except (aiohttp.ContentTypeError, ValueError):
                    payload = None
                return response.status, payload
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise RcProxyError("crm_unavailable", 502) from error


async def _call_crm(method: str, path: str, json_body: dict | None = None) -> object:
    """Выполняет разрешённый запрос к CRM и нормализует ошибки."""
    url = f"{_crm_base_url()}{path}"
    headers = {
        "Authorization": f"Bearer {_crm_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    status, payload = await _send_http(method, url, headers, json_body)
    if status >= 400:
        raise RcProxyError(_error_code(status, payload), status)
    return payload


def _no_store(payload: object, status: int = 200) -> web.Response:
    return web.json_response(
        payload, status=status, headers={"Cache-Control": "no-store, max-age=0"}
    )


def _guard(
    handler: Callable[[web.Request], Awaitable[web.Response]],
) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Превращает RcProxyError в безопасный JSON-ответ с кодом ошибки."""

    async def wrapped(request: web.Request) -> web.Response:
        try:
            return await handler(request)
        except RcProxyError as error:
            return web.json_response(
                {"error": error.code},
                status=error.status,
                headers={"Cache-Control": "no-store, max-age=0"},
            )

    return wrapped


async def _list_handler(request: web.Request) -> web.Response:
    _require_browser_session(request)
    method, path = _resolve_target("list_publications", {})
    return _no_store(await _call_crm(method, path))


async def _create_command_handler(request: web.Request) -> web.Response:
    _require_browser_session(request)
    publication_id = _validate_publication_id(request.match_info["publication_id"])
    body = await _read_command_body(request)
    method, path = _resolve_target("create_command", {"publication_id": publication_id})
    return _no_store(await _call_crm(method, path, body), status=201)


async def _close_handler(request: web.Request) -> web.Response:
    _require_browser_session(request)
    publication_id = _validate_publication_id(request.match_info["publication_id"])
    method, path = _resolve_target("close_publication", {"publication_id": publication_id})
    return _no_store(await _call_crm(method, path))


list_handler = _guard(_list_handler)
create_command_handler = _guard(_create_command_handler)
close_handler = _guard(_close_handler)
