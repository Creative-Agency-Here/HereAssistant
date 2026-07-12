"""/api/now — текущая активная задача (реальные данные из bridge.sqlite3)."""

from __future__ import annotations

from aiohttp import web

from webapp.api import repo


async def handler(request: web.Request) -> web.Response:
    user_id = int(request["user"]["id"])
    task = repo.get_active_task(user_id)
    actions = repo.get_recent_actions(user_id, limit=5)

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
