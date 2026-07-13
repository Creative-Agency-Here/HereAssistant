"""HereAssistant Web API — aiohttp.

Поднимается отдельным процессом, читает ту же bridge.sqlite3 что и бот.
Локальная разработка:   python webapp/api/server.py
Production через PM2:    pm2 start ecosystem.config.js
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

# чтобы импортировался core при запуске из любого места
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web

from core import config
from webapp.api.auth import validate_init_data
from webapp.api.routes import changes as route_changes
from webapp.api.routes import git_connections as route_git_connections
from webapp.api.routes import history as route_history
from webapp.api.routes import now as route_now
from webapp.api.routes import rtk as route_rtk
from webapp.api.routes import status as route_status
from webapp.api.routes import tasks as route_tasks
from webapp.api.routes import ws as route_ws

log = logging.getLogger("webapp.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")


def _dev_skip_auth(env: Mapping[str, str]) -> bool:
    """Skip-auth требует двух явных флагов и не включается в production случайно."""
    environment = env.get("HEREASSISTANT_ENV", "production").strip().lower()
    requested = env.get("WEBAPP_DEV_SKIP_AUTH", "0").strip().lower()
    return environment == "development" and requested in ("1", "true", "yes")


# Одного WEBAPP_DEV_SKIP_AUTH недостаточно: нужен явный development-контур.
DEV_SKIP_AUTH = _dev_skip_auth(os.environ)
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8200"))
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "127.0.0.1")
WEBAPP_DOMAIN = os.environ.get("WEBAPP_DOMAIN", "").strip()


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # health-check без авторизации
    if request.path in ("/api/health", "/health"):
        return await handler(request)
    # OAuth callback has no Telegram header after a third-party redirect. Its
    # single-use HMAC-bound state is validated inside the route.
    if request.path == "/api/git/oauth/callback/gitea":
        return await handler(request)

    # Сервисный API (/api/v1/*) — ТОЛЬКО Bearer SERVICE_API_TOKEN.
    # Пустой токен = сервисный API отключён (503), а не открыт.
    if request.path.startswith("/api/v1/"):
        if not config.SERVICE_API_TOKEN:
            return web.json_response({"error": "service api disabled"}, status=503)
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token or not hmac.compare_digest(token, config.SERVICE_API_TOKEN):
            return web.json_response({"error": "unauthorized"}, status=401)
        request["service"] = True
        return await handler(request)

    if DEV_SKIP_AUTH:
        request["user"] = {"id": config.ADMIN_ID or 0, "first_name": "dev", "username": "dev"}
        return await handler(request)

    init_data = request.headers.get("Authorization", "")
    if init_data.startswith("tma "):
        init_data = init_data[4:]
    elif not init_data:
        # WebSocket в браузере не умеет слать заголовки — initData приходит в ?tma=
        init_data = request.query.get("tma", "")
    user = validate_init_data(init_data)
    # Фолбэк для браузера/десктопа (нет Telegram initData): секретный ключ.
    if not user and config.WEBAPP_ACCESS_KEY:
        key = request.headers.get("X-Access-Key", "") or request.query.get("key", "")
        if key and hmac.compare_digest(key, config.WEBAPP_ACCESS_KEY):
            user = {"id": config.ADMIN_ID or 0, "first_name": "key", "username": "key"}
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
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Access-Key"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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
    return web.json_response({"ok": True, "version": config.APP_VERSION})


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware, auth_middleware])
    app.router.add_get("/api/health", health)
    app.router.add_get("/health", health)  # alias для nginx/uptime-мониторинга
    app.router.add_get("/api/status", route_status.handler)
    app.router.add_get("/api/now", route_now.handler)
    app.router.add_get("/api/history", route_history.list_handler)
    app.router.add_get("/api/history/{conv_id}", route_history.get_handler)
    app.router.add_get("/api/changes", route_changes.list_handler)
    app.router.add_get("/api/rtk", route_rtk.handler)
    app.router.add_get("/api/git/connections", route_git_connections.list_handler)
    app.router.add_post("/api/git/connections/start", route_git_connections.start_handler)
    app.router.add_get("/api/git/oauth/callback/gitea", route_git_connections.callback_handler)
    app.router.add_delete(
        "/api/git/connections/{connection_id}", route_git_connections.revoke_handler
    )
    app.router.add_post(
        "/api/git/connections/{connection_id}/refresh",
        route_git_connections.refresh_handler,
    )
    app.router.add_get(
        "/api/git/connections/{connection_id}/repositories",
        route_git_connections.repositories_handler,
    )
    app.router.add_post(
        "/api/git/connections/{connection_id}/repositories/{repository_id}/grant",
        route_git_connections.repository_grant_handler,
    )
    app.router.add_delete(
        "/api/git/connections/{connection_id}/repositories/{repository_id}/grant",
        route_git_connections.repository_grant_handler,
    )
    app.router.add_get("/ws", route_ws.handler)
    # Сервисный API (SERVICE_API_TOKEN; private/local проекты невидимы)
    app.router.add_post("/api/v1/tasks", route_tasks.create)
    app.router.add_get("/api/v1/tasks", route_tasks.list_)
    app.router.add_get("/api/v1/tasks/{task_id}", route_tasks.get)
    app.router.add_patch("/api/v1/tasks/{task_id}", route_tasks.patch)
    return app


def main():
    config.init_dirs()
    log.info(
        "Web API starting on %s:%s (dev_skip_auth=%s)", WEBAPP_HOST, WEBAPP_PORT, DEV_SKIP_AUTH
    )
    web.run_app(create_app(), host=WEBAPP_HOST, port=WEBAPP_PORT, print=None)


if __name__ == "__main__":
    main()
