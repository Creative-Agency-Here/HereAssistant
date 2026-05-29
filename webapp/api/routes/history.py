"""/api/history — список диалогов и просмотр одного."""

from __future__ import annotations

from aiohttp import web

from webapp.api import repo


async def list_handler(request: web.Request) -> web.Response:
    try:
        limit = min(int(request.query.get("limit", 50)), 200)
        offset = max(int(request.query.get("offset", 0)), 0)
    except ValueError:
        return web.json_response({"error": "bad pagination"}, status=400)
    account = request.query.get("account")
    q = request.query.get("q")

    items = repo.list_conversations(limit=limit, offset=offset, account=account, q=q)
    return web.json_response({"items": items, "limit": limit, "offset": offset})


async def get_handler(request: web.Request) -> web.Response:
    try:
        conv_id = int(request.match_info["conv_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "bad conv_id"}, status=400)
    conv = repo.get_conversation(conv_id)
    if not conv:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(conv)
