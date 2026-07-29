"""/api/changes — журнал изменений файлов (таблица file_changes)."""

from __future__ import annotations

from aiohttp import web

from webapp.api import repo
from webapp.api.pagination import bounded_limit, bounded_offset


async def list_handler(request: web.Request) -> web.Response:
    try:
        limit = bounded_limit(request.query.get("limit"))
        offset = bounded_offset(request.query.get("offset"))
    except ValueError:
        return web.json_response({"error": "bad pagination"}, status=400)

    file = request.query.get("file")

    def _int(name):
        v = request.query.get(name)
        return int(v) if v and v.lstrip("-").isdigit() else None

    thread_id = _int("thread") if _int("thread") is not None else _int("thread_id")
    since = _int("since")
    until = _int("until")

    items = repo.list_file_changes(
        user_id=int(request["user"]["id"]),
        limit=limit,
        offset=offset,
        file=file,
        thread_id=thread_id,
        since=since,
        until=until,
    )
    return web.json_response({"items": items, "limit": limit, "offset": offset})
