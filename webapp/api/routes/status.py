"""GET /api/status — состояние сервиса без приватного контента.

Под обычной WebApp-авторизацией. Отдаёт только метаданные: доступность БД,
наличие runtime-каталогов, счётчики строк. Никаких prompt/result/diff.
"""

from __future__ import annotations

import sqlite3

from aiohttp import web

from core import config


def _counts() -> dict:
    try:
        c = sqlite3.connect(config.DB_PATH)
        try:
            out = {}
            for table in ("conversations", "messages", "events", "file_changes", "tasks"):
                try:
                    out[table] = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.OperationalError:
                    out[table] = None
            return {"db_ok": True, "counts": out}
        finally:
            c.close()
    except Exception:
        return {"db_ok": False, "counts": {}}


async def handler(request: web.Request) -> web.Response:
    db_info = _counts()
    return web.json_response({
        "ok": True,
        "version": config.APP_VERSION,
        "db_ok": db_info["db_ok"],
        "counts": db_info["counts"],
        "runtime_dirs": {
            "runtime": config.RUNTIME_DIR.exists(),
            "logs": config.LOGS_DIR.exists(),
            "cli_homes": config.CLI_HOMES_DIR.exists(),
            "workspace": config.WORKSPACE_DIR.exists(),
        },
        "service_api_enabled": bool(config.SERVICE_API_TOKEN),
    })
