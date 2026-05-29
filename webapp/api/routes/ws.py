"""/ws — WebSocket: стрим хвоста bot.log + статус (раз в 2 сек).

В MVP — простой tail файла, без Redis. При желании потом подменим на pub/sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from aiohttp import WSMsgType, web

from core import config
from webapp.api import repo

log = logging.getLogger("webapp.ws")

LOG_FILE = config.LOGS_DIR / "bot.log"
TICK_SEC = float(os.environ.get("WS_TICK_SEC", "2.0"))
MAX_LINES_INIT = 50      # сколько строк лога присылаем на первом подключении


async def handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    log.info("ws client connected user=%s", request["user"].get("id"))

    # начальный дамп: последние N строк лога
    last_inode_pos = await _initial_dump(ws)

    try:
        while not ws.closed:
            await asyncio.sleep(TICK_SEC)
            # 1) новые строки лога
            last_inode_pos = await _stream_new_lines(ws, last_inode_pos)
            # 2) статус задачи
            await _send_status(ws)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning("ws loop error: %s", e)
    finally:
        if not ws.closed:
            await ws.close()
        log.info("ws client disconnected user=%s", request["user"].get("id"))
    return ws


async def _initial_dump(ws: web.WebSocketResponse) -> int:
    """Послать последние MAX_LINES_INIT строк, вернуть текущую позицию в файле."""
    if not LOG_FILE.exists():
        return 0
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 64 * 1024)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace").splitlines()
        lines = tail[-MAX_LINES_INIT:]
        await ws.send_str(json.dumps({"type": "log_init", "lines": lines}))
        return size
    except Exception as e:
        log.warning("initial log dump failed: %s", e)
        return 0


async def _stream_new_lines(ws: web.WebSocketResponse, last_pos: int) -> int:
    if not LOG_FILE.exists():
        return last_pos
    try:
        size = LOG_FILE.stat().st_size
        if size <= last_pos:
            # ротация — файл стал меньше, начинаем сначала
            if size < last_pos:
                last_pos = 0
            return last_pos
        with open(LOG_FILE, "rb") as f:
            f.seek(last_pos)
            data = f.read(size - last_pos)
        new_lines = data.decode("utf-8", errors="replace").splitlines()
        if new_lines:
            await ws.send_str(json.dumps({"type": "log_append", "lines": new_lines}))
        return size
    except Exception as e:
        log.warning("stream log failed: %s", e)
        return last_pos


async def _send_status(ws: web.WebSocketResponse):
    try:
        task = repo.get_active_task()
        actions = repo.get_recent_actions(limit=5)
        await ws.send_str(json.dumps({
            "type": "status",
            "task": task,
            "recent_actions": actions,
        }, ensure_ascii=False))
    except Exception as e:
        log.warning("send status failed: %s", e)
