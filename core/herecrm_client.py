"""Узкий read-only клиент HereAssistant -> HereCRM для Mini App.

Использует тот же scoped ``has_`` токен, что outbox, но только для ручек с
``sessions:read``. Токен не попадает в SQLite, ответы не кешируются локально.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from . import config


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


async def _get(path: str, params: dict[str, str] | None = None) -> Any:
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                endpoint(path),
                params=params,
                headers={"Authorization": f"Bearer {config.HERECRM_SYNC_TOKEN}"},
            ) as response:
                if response.status in (401, 403):
                    raise HereCrmClientError("crm_token_needs_read_scope", 424)
                if response.status >= 400:
                    raise HereCrmClientError("crm_unavailable", 502)
                try:
                    return await response.json()
                except (aiohttp.ContentTypeError, ValueError) as error:
                    raise HereCrmClientError("crm_invalid_response", 502) from error
    except HereCrmClientError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise HereCrmClientError("crm_unavailable", 502) from error


async def conversations(*, channel: str | None = None, provider: str | None = None) -> Any:
    params = {
        key: value
        for key, value in (("channel", channel), ("provider", provider))
        if value
    }
    return await _get("conversations", params or None)


async def digest(days: int) -> Any:
    return await _get("digest", {"days": str(min(90, max(1, days)))})


async def feed(conversation_id: str, *, cursor: str | None = None, limit: int = 60) -> Any:
    params = {"limit": str(min(100, max(20, limit)))}
    if cursor:
        params["cursor"] = cursor
    safe_id = quote(conversation_id, safe="")
    return await _get(f"conversations/{safe_id}/feed", params)
