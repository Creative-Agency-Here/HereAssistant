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

Доступ — только у ВЛАДЕЛЬЦА серверного токена: сессия из контура CRM есть у любого
участника пространства, а исходящий запрос уходит от имени владельца устройства,
поэтому ``crm_user_id`` сессии сверяется с ``RC_PROXY_CRM_OWNER_USER_ID``. Не задана
переменная — прокси выключен (``rc_not_configured``), несовпадение — ``not_owner``.

Всё, что не входит в allowlist маршрутов/методов, отклоняется до любого
исходящего запроса. Произвольные заголовки браузера не пересылаются: наружу
уходит ровно один заголовок из запроса браузера — ``Idempotency-Key`` при
постановке команды, и только если он проходит строгую маску. Без него сервер
генерирует ключ сам, и повтор после сетевого сбоя создаёт ВТОРОЙ промпт, то есть
второй запуск агента на устройстве.
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

# Потолок самого промпта в символах: тот же, что у RcPromptPayloadDto бэкенда
# (MaxLength(8000)). Отбиваем здесь, чтобы человек получил понятную причину, а не
# общий 400 из глубины валидатора.
MAX_PROMPT_CHARS = 8000

# Единственный ключ payload команды prompt. Раннер читает именно payload["prompt"]
# (chat_remote_control._ingest_prompt_command), поэтому любой другой ключ означал
# бы запуск агента с ПУСТЫМ промптом — тихо и без ошибки. Такое тело отклоняем.
_PROMPT_PAYLOAD_KEY = "prompt"

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

# Ключ идемпотентности: непрозрачная строка, которую бэкенд кладёт в уникальный
# индекс (publication_id, idempotency_key). Маска узкая специально — заголовок
# приходит из браузера, а всё, что уходит наружу под контролем пользователя,
# обязано быть проверено, а не переслано как есть.
_IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

# Коды отказа, которые бэкенд кладёт в тело ответа на постановку команды
# (errorCode). Список закрытый и совпадает с RcServerErrorCode фронта: только эти
# строки прокси признаёт причиной, найденной в message. Коды исполнения
# (PRIVACY_DENIED и прочие из отчёта раннера) сюда не относятся — они приходят
# статусом команды, а не HTTP-ошибкой.
_KNOWN_ERROR_CODES = (
    "DEVICE_OFFLINE",
    "PUBLICATION_CLOSED",
    "CAPABILITY_UNAVAILABLE",
    "IDEMPOTENCY_KEY_REQUIRED",
    "IDEMPOTENCY_KEY_INVALID",
)


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


def _owner_crm_user_id() -> int:
    """Владелец серверного CRM-токена прокси. Не задан = прокси выключен.

    Исходящий запрос уходит с общим ``RC_PROXY_CRM_TOKEN``, то есть ОТ ИМЕНИ
    владельца устройства. Пока проверялась только принадлежность сессии контуру
    CRM, любой участник пространства, вошедший в Mini App через SSO HereCRM, мог
    поставить промпт на компьютер владельца (произвольное выполнение кода в его
    рабочем каталоге) и закрыть его публикацию — отмены команды в контракте нет.

    Поэтому владелец задаётся ЯВНО отдельной переменной и сверяется с
    ``crm_user_id`` сессии. Вывести владельца из самого токена нельзя: он
    непрозрачный, а ``/hereassistant-sync/sso/exchange`` отвечает на одноразовый
    тикет и об owner-е токена ничего не говорит. Не задана переменная — прокси
    выключен целиком (default deny), а не «пускаем всех».
    """
    raw = os.environ.get("RC_PROXY_CRM_OWNER_USER_ID", "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise RcProxyError("rc_not_configured", 503)
    return int(raw)


def _require_browser_session(request: web.Request) -> dict:
    """Пропускает только сессию ВЛАДЕЛЬЦА серверного токена прокси.

    Глобальный auth_middleware может пустить запрос по Telegram initData или
    dev-skip, но управление чужой сессией /rc требует именно входа через SSO
    HereCRM: только тогда browser_session несёт crm_user_id и tenant_id.

    Одного признака «сессия из контура CRM» недостаточно: такую сессию получает
    ЛЮБОЙ участник пространства, обменявший свой ``hat_``-тикет, а исходящий
    запрос уходит от имени владельца токена. Поэтому здесь же сверяется
    ``crm_user_id`` с владельцем (``RC_PROXY_CRM_OWNER_USER_ID``); несовпадение —
    403, а не «наверное свой».
    """
    token = request.cookies.get(browser_session.COOKIE_NAME, "")
    session = browser_session.read(token)
    if not session or session.get("auth_source") != "crm":
        raise RcProxyError("unauthorized", 401)
    owner_id = _owner_crm_user_id()
    try:
        session_user_id = int(session.get("crm_user_id") or 0)
    except (TypeError, ValueError):
        session_user_id = 0
    if session_user_id != owner_id:
        raise RcProxyError("not_owner", 403)
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


async def _read_body_bytes(request: web.Request) -> bytes:
    """Читает тело целиком, но не больше потолка.

    ``StreamReader.read(n)`` отдаёт то, что уже в буфере, то есть МЕНЬШЕ ``n``:
    одного вызова достаточно только для короткого тела. Длинный валидный промпт
    из-за этого обрезался посередине и отбивался как ``invalid_json``, хотя был
    заметно ниже лимита. Поэтому читаем в цикле до EOF и отдельно проверяем
    заявленный Content-Length, чтобы «слишком большое» было именно 413.
    """
    declared = request.content_length
    if declared is not None and declared > MAX_BODY_BYTES:
        raise RcProxyError("body_too_large", 413)
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_BODY_BYTES:
        chunk = await request.content.read(MAX_BODY_BYTES + 1 - size)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
    raise RcProxyError("body_too_large", 413)


async def _read_command_body(request: web.Request) -> dict:
    """Читает тело команды с потолком размера и валидацией типа команды."""
    raw = await _read_body_bytes(request)
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
    if command_type == "prompt":
        payload = _validated_prompt_payload(payload)
    command: dict = {"commandType": command_type}
    if payload is not None:
        command["payload"] = payload
    return command


def _validated_prompt_payload(payload: dict | None) -> dict:
    """Сводит payload промпта к единственному разрешённому ключу.

    Промпт с чужим ключом (исторически ``text``) доезжал до устройства пустой
    строкой: агент запускался, ничего не делал и отчитывался успехом. Поэтому
    здесь строгий отказ, а не «переименуем по-тихому»: браузер обязан прислать
    ровно ``prompt``.
    """
    prompt = (payload or {}).get(_PROMPT_PAYLOAD_KEY)
    if not isinstance(prompt, str) or not prompt.strip():
        raise RcProxyError("invalid_command", 400)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise RcProxyError("prompt_too_long", 400)
    return {_PROMPT_PAYLOAD_KEY: prompt}


def _idempotency_key(request: web.Request) -> str | None:
    """Ключ идемпотентности браузера. Отсутствует — None, кривой — отказ.

    Молча отбрасывать невалидный ключ нельзя: браузер считал бы повтор безопасным,
    а сервер завёл бы вторую команду. Лучше явный отказ, чем тихий дубль запуска.
    """
    raw = request.headers.get(_IDEMPOTENCY_KEY_HEADER)
    if raw is None:
        return None
    key = raw.strip()
    if not _IDEMPOTENCY_KEY_RE.match(key):
        raise RcProxyError("invalid_idempotency_key", 400)
    return key


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


async def _call_crm(
    method: str,
    path: str,
    json_body: dict | None = None,
    idempotency_key: str | None = None,
) -> object:
    """Выполняет разрешённый запрос к CRM и нормализует ошибки."""
    url = f"{_crm_base_url()}{path}"
    headers = {
        "Authorization": f"Bearer {_crm_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if idempotency_key is not None:
        headers[_IDEMPOTENCY_KEY_HEADER] = idempotency_key
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
    idempotency_key = _idempotency_key(request)
    body = await _read_command_body(request)
    method, path = _resolve_target("create_command", {"publication_id": publication_id})
    return _no_store(
        await _call_crm(method, path, body, idempotency_key=idempotency_key), status=201
    )


async def _close_handler(request: web.Request) -> web.Response:
    _require_browser_session(request)
    publication_id = _validate_publication_id(request.match_info["publication_id"])
    method, path = _resolve_target("close_publication", {"publication_id": publication_id})
    return _no_store(await _call_crm(method, path))


list_handler = _guard(_list_handler)
create_command_handler = _guard(_create_command_handler)
close_handler = _guard(_close_handler)
