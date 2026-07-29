"""Узкий клиент HereAssistant -> HereCRM для Mini App и Telegram-моста /rc.

Оба набора ручек живут под ОДНИМ префиксом ``hereassistant-sync`` и ходят одним
scoped ``has_``-токеном; разные у них только scopes:

* ручки Mini App (``conversations``, ``digest``, ``feed``, ``sso/exchange``) —
  scopes ``sessions:read``/``sessions:write``;
* ручки удалённого управления (``hereassistant-sync/rc/*``) — scopes
  ``rc:read``/``rc:command``. Их четыре: список живых публикаций, постановка
  команды, состояние команды и журнал событий курсором. Ничего другого на этом
  контуре нет: публикацию из Telegram не закрыть, git-команд не поставить,
  текст ответа тут не лежит (он приходит из ленты сессии CRM).

Маршруты ``hereassistant-sync/remote-publications*`` не существуют вовсе, а
``cli-agent/remote-publications``/``cli-agent/runner`` — это ДРУГИЕ контуры
(браузерный пользовательский JWT и device-токен раннера). Бот в них не ходит.

Токены не попадают в SQLite, ответы не кешируются локально, тела ответов сервера
в ошибки и логи не просачиваются — наружу отдаётся только код.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from . import config

# Статус 0 означает «ответ пришёл, но это не JSON» — общий вход для мапперов.
_INVALID_RESPONSE = 0


class HereCrmClientError(RuntimeError):
    """Безопасная ошибка зависимости без тела ответа HereCRM."""

    def __init__(self, code: str, status: int = 502):
        super().__init__(code)
        self.code = code
        self.status = status


def configured() -> bool:
    return bool(config.HERECRM_SYNC_URL and config.HERECRM_SYNC_TOKEN)


def endpoint(path: str) -> str:
    parsed = urlparse(config.HERECRM_SYNC_URL)
    if not configured() or parsed.scheme != "https" or not parsed.netloc:
        raise HereCrmClientError("crm_not_configured", 503)
    return f"{config.HERECRM_SYNC_URL}/hereassistant-sync/{path.lstrip('/')}"


def rc_configured() -> bool:
    """Контур /rc — тот же URL и тот же ``has_``-токен, что и sync истории.

    Отдельных переменных у канала нет. Права решаются scopes токена: без
    ``rc:read``/``rc:command`` сервер ответит 403, и бот честно скажет об этом.
    """
    return configured()


def rc_endpoint(path: str) -> str:
    """Маршрут владельца публикации под ``hereassistant-sync/rc``.

    Это единственный существующий контур /rc для ``has_``-токена. Ошибка в
    префиксе даёт 404 на каждом вызове и выглядит как «публикация не найдена».
    """
    parsed = urlparse(config.HERECRM_SYNC_URL)
    if not rc_configured() or parsed.scheme != "https" or not parsed.netloc:
        raise HereCrmClientError("rc_not_configured", 503)
    return f"{config.HERECRM_SYNC_URL}/hereassistant-sync/rc/{path.lstrip('/')}".rstrip("/")


ErrorMapper = Callable[[int], HereCrmClientError]


def _read_error(status: int) -> HereCrmClientError:
    if status in (401, 403):
        return HereCrmClientError("crm_token_needs_read_scope", 424)
    if status == _INVALID_RESPONSE:
        return HereCrmClientError("crm_invalid_response", 502)
    return HereCrmClientError("crm_unavailable", 502)


def _write_error(status: int) -> HereCrmClientError:
    if status in (401, 403):
        return HereCrmClientError("crm_sso_denied", 403)
    if status == _INVALID_RESPONSE:
        return HereCrmClientError("crm_invalid_response", 502)
    return HereCrmClientError("crm_unavailable", 502)


def _rc_error(status: int) -> HereCrmClientError:
    """Коды /rc отделены от Mini App: бот по ним снимает привязку треда."""
    if status in (401, 403):
        return HereCrmClientError("rc_forbidden", 403)
    if status == 404:
        return HereCrmClientError("rc_not_found", 404)
    if status == 409:
        return HereCrmClientError("rc_publication_closed", 409)
    return HereCrmClientError("rc_unavailable", 502)


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    json_body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    error_for: ErrorMapper,
    url: str | None = None,
) -> Any:
    # Токен один на оба контура: секрет живёт только в окружении процесса.
    request_headers = {"Authorization": f"Bearer {config.HERECRM_SYNC_TOKEN}"}
    if headers:
        request_headers.update(headers)
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                url if url is not None else endpoint(path),
                params=params,
                json=json_body,
                headers=request_headers,
            ) as response:
                if response.status >= 400:
                    raise error_for(response.status)
                try:
                    return await response.json()
                except (aiohttp.ContentTypeError, ValueError) as error:
                    raise error_for(_INVALID_RESPONSE) from error
    except HereCrmClientError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise error_for(502) from error


async def _get(path: str, params: dict[str, str] | None = None) -> Any:
    return await _request("GET", path, params=params, error_for=_read_error)


async def _post(path: str, payload: dict[str, str]) -> Any:
    return await _request("POST", path, json_body=payload, error_for=_write_error)


async def conversations(*, channel: str | None = None, provider: str | None = None) -> Any:
    params = {key: value for key, value in (("channel", channel), ("provider", provider)) if value}
    return await _get("conversations", params or None)


async def digest(days: int) -> Any:
    return await _get("digest", {"days": str(min(90, max(1, days)))})


async def feed(conversation_id: str, *, cursor: str | None = None, limit: int = 60) -> Any:
    params = {"limit": str(min(100, max(20, limit)))}
    if cursor:
        params["cursor"] = cursor
    safe_id = quote(conversation_id, safe="")
    return await _get(f"conversations/{safe_id}/feed", params)


async def exchange_sso_ticket(ticket: str) -> Any:
    return await _post("sso/exchange", {"ticket": ticket})


# --- Удалённое управление (/rc): owner-scoped маршруты hereassistant-sync ----
# Четыре маршрута, и других у канала нет:
#   GET  rc/publications                          — живые публикации владельца;
#   POST rc/publications/:id/commands             — постановка prompt|stop;
#   GET  rc/publications/:id/commands/:commandId  — состояние команды;
#   GET  rc/publications/:id/events?afterId=      — журнал событий курсором.
#
# tenant и владелец на сервере берутся ИЗ ТОКЕНА, поэтому бот не может дотянуться
# до чужой публикации: заголовки пространства и идентификаторы не передаются.

# Потолок страницы журнала на сервере.
RC_EVENTS_MAX_LIMIT = 200


async def rc_publications(*, state: str = "live", device_id: str | None = None) -> Any:
    """Публикации владельца токена.

    ``state='all'`` нужен для диагностики: закрытую публикацию видно, и бот может
    сказать «публикация закрыта» вместо расплывчатого «нет публикаций».
    """
    params = {"state": state if state in ("live", "all") else "live"}
    if device_id:
        params["deviceId"] = device_id
    return await _request(
        "GET",
        "",
        params=params,
        error_for=_rc_error,
        url=rc_endpoint("publications"),
    )


async def rc_create_command(
    publication_id: str,
    *,
    command_type: str,
    payload: Mapping[str, Any] | None = None,
    idempotency_key: str,
) -> Any:
    """Ставит команду. Ключ идемпотентности живёт в заголовке, а не в теле.

    Повтор с тем же ключом дублей не создаёт: сервер отдаёт уже существующую
    команду с её ТЕКУЩИМ статусом и ``created: false``. Опрашивать статус этим
    повтором ЗАПРЕЩЕНО — для чтения есть ``rc_command_state``.
    """
    safe_id = quote(publication_id, safe="")
    body: dict[str, Any] = {"commandType": command_type}
    if payload:
        # Пустой payload не отправляем вовсе: маршрут валидируется с
        # forbidNonWhitelisted, и лишние ключи там не молча срезаются, а падают.
        body["payload"] = dict(payload)
    return await _request(
        "POST",
        "",
        json_body=body,
        headers={"Idempotency-Key": idempotency_key},
        error_for=_rc_error,
        url=rc_endpoint(f"publications/{safe_id}/commands"),
    )


async def rc_command_state(publication_id: str, command_id: str) -> Any:
    """Состояние команды — ЕДИНСТВЕННЫЙ источник истины по её статусу."""
    safe_publication = quote(publication_id, safe="")
    safe_command = quote(command_id, safe="")
    return await _request(
        "GET",
        "",
        error_for=_rc_error,
        url=rc_endpoint(f"publications/{safe_publication}/commands/{safe_command}"),
    )


async def rc_events(
    publication_id: str,
    *,
    after_id: str | None = None,
    limit: int = 100,
    command_id: str | None = None,
) -> Any:
    """Журнал публикации курсором по ``id`` (bigserial, монотонный).

    Курсор передаётся и возвращается СТРОКОЙ: bigint не влезает в число JS, и
    сортировка по времени тут не годится — у пачки событий одна транзакция.
    """
    params = {"limit": str(min(RC_EVENTS_MAX_LIMIT, max(1, limit)))}
    if after_id:
        params["afterId"] = str(after_id)
    if command_id:
        params["commandId"] = command_id
    safe_id = quote(publication_id, safe="")
    return await _request(
        "GET",
        "",
        params=params,
        error_for=_rc_error,
        url=rc_endpoint(f"publications/{safe_id}/events"),
    )
