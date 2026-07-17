"""Owner-only прокси CRM-сессий в HereAssistant Mini App."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from core import config, herecrm_client


def _require_owner(request: web.Request) -> None:
    user_id = int(request["user"]["id"])
    if config.ADMIN_ID is None or user_id != config.ADMIN_ID:
        raise web.HTTPForbidden(
            text='{"error":"crm_owner_only"}',
            content_type="application/json",
        )


async def _response(
    request: web.Request,
    operation: Callable[[], Awaitable[Any]],
) -> web.Response:
    _require_owner(request)
    try:
        return web.json_response(await operation())
    except herecrm_client.HereCrmClientError as error:
        return web.json_response({"error": error.code}, status=error.status)


async def conversations_handler(request: web.Request) -> web.Response:
    return await _response(
        request,
        lambda: herecrm_client.conversations(
            channel=request.query.get("channel"), provider=request.query.get("provider")
        ),
    )


async def digest_handler(request: web.Request) -> web.Response:
    try:
        days = int(request.query.get("days", "7"))
    except ValueError:
        return web.json_response({"error": "bad_period"}, status=400)
    return await _response(request, lambda: herecrm_client.digest(days))


async def feed_handler(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", "60"))
    except ValueError:
        return web.json_response({"error": "bad_pagination"}, status=400)
    return await _response(
        request,
        lambda: herecrm_client.feed(
            request.match_info["conversation_id"],
            cursor=request.query.get("cursor"),
            limit=limit,
        ),
    )
