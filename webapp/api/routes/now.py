"""/api/now — текущая активная задача (реальные данные из bridge.sqlite3)."""

from __future__ import annotations

from aiohttp import web

from webapp.api import repo


async def handler(request: web.Request) -> web.Response:
    task = repo.get_active_task()
    actions = repo.get_recent_actions(limit=5)

    if task is None:
        payload = {
            "active": False,
            "recent_actions": actions,
            "user": request["user"],
        }
    else:
        payload = {
            **task,
            "recent_actions": actions,
            "user": request["user"],
        }
    return web.json_response(payload)
