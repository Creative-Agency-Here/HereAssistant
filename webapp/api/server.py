"""HereAssistant Web API — aiohttp.

Поднимается отдельным процессом, читает ту же bridge.sqlite3 что и бот.
Локальная разработка:   python webapp/api/server.py
Production через PM2:    pm2 start ecosystem.config.js
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# чтобы импортировался core при запуске из любого места
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web

from core import config
from webapp.api.auth import validate_init_data
from webapp.api.routes import now as route_now
from webapp.api.routes import history as route_history
from webapp.api.routes import ws as route_ws
from webapp.api.routes import changes as route_changes

log = logging.getLogger("webapp.api")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s %(message)s")

# В dev-режиме можно пропустить initData (false по умолчанию — строго проверяем).
DEV_SKIP_AUTH = os.environ.get("WEBAPP_DEV_SKIP_AUTH", "0") in ("1", "true", "yes")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8200"))
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "127.0.0.1")
WEBAPP_DOMAIN = os.environ.get("WEBAPP_DOMAIN", "").strip()


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # health-check без авторизации
    if request.path in ("/api/health",):
        return await handler(request)

    if DEV_SKIP_AUTH:
        request["user"] = {"id": config.ADMIN_ID or 0, "first_name": "dev",
                            "username": "dev"}
        return await handler(request)

    init_data = request.headers.get("Authorization", "")
    if init_data.startswith("tma "):
        init_data = init_data[4:]
    elif not init_data:
        # WebSocket в браузере не умеет слать заголовки — initData приходит в ?tma=
        init_data = request.query.get("tma", "")
    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)

    request["user"] = user
    return await handler(request)


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Минимальный CORS под Mini App. В проде разрешаем только WEBAPP_DOMAIN."""
    if request.method == "OPTIONS":
        return _cors_response(web.Response(status=204), request)
    resp = await handler(request)
    return _cors_response(resp, request)


def _cors_response(resp: web.StreamResponse, request: web.Request) -> web.StreamResponse:
    origin = request.headers.get("Origin", "")
    allowed = _allowed_origin(origin)
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = allowed
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


def _allowed_origin(origin: str) -> str:
    if not origin:
        return ""
    # локальная разработка
    if origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
        return origin
    # прод-домен Mini App
    if WEBAPP_DOMAIN and origin == f"https://{WEBAPP_DOMAIN}":
        return origin
    return ""


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": "0.1.0"})


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware, auth_middleware])
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/now", route_now.handler)
    app.router.add_get("/api/history", route_history.list_handler)
    app.router.add_get("/api/history/{conv_id}", route_history.get_handler)
    app.router.add_get("/api/changes", route_changes.list_handler)
    app.router.add_get("/ws", route_ws.handler)
    return app


def main():
    config.init_dirs()
    log.info("Web API starting on %s:%s (dev_skip_auth=%s)",
             WEBAPP_HOST, WEBAPP_PORT, DEV_SKIP_AUTH)
    web.run_app(create_app(), host=WEBAPP_HOST, port=WEBAPP_PORT, print=None)


if __name__ == "__main__":
    main()
