"""Безопасная витрина каналов HereAssistant без credentials и путей auth-home."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from aiohttp import web

from core import config, herecrm_client
from core import contours as contour_store
from core.workspace_status import installation_identity, parse_activity_at, workspace_overview
from webapp.api import repo

log = logging.getLogger("bridge.webapp")

_T = TypeVar("_T")


def _safe(source: Callable[[], _T], default: _T, *, what: str) -> _T:
    """Читает часть дашборда, не роняя весь экран.

    Главный экран Mini App собирает данные из полутора десятков запросов к SQLite.
    Достаточно одной блокировки БД (её легко создаёт фоновая запись событий), и
    пользователь вместо дашборда получал 500 и пустую страницу. Частичные данные
    полезнее пустоты, а о деградации говорит поле `degraded` в ответе.
    """
    try:
        return source()
    except (sqlite3.Error, OSError) as error:
        log.warning("дашборд: не удалось получить %s: %s", what, error)
        return default


def _crm_sessions(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "conversations", "sessions"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _contours(local: dict[str, str], sessions: list[dict], *, local_working: bool) -> list[dict]:
    now = datetime.now(UTC)
    grouped: dict[str, dict] = {
        local["id"]: {
            **local,
            "local": True,
            "state": "working" if local_working else "open",
            "estimated": False,
            "sessions": 0,
            "lastActivityAt": None,
        }
    }
    for session in sessions:
        host = " ".join(str(session.get("originHost") or "").split()).strip()
        if not host:
            continue
        key = host.casefold()
        current = grouped.setdefault(
            key,
            {
                "id": key,
                "label": host[:80],
                "kind": "remote",
                "originHost": host[:120],
                "local": False,
                "state": "closed",
                "estimated": True,
                "sessions": 0,
                "lastActivityAt": None,
            },
        )
        current["sessions"] += 1
        raw_activity = session.get("lastActivityAt") or session.get("createdAt")
        activity = parse_activity_at(raw_activity)
        previous = parse_activity_at(current["lastActivityAt"])
        if activity and (previous is None or activity > previous):
            current["lastActivityAt"] = activity.isoformat()
        if current["local"] and local_working:
            current["state"] = "working"
        elif activity and (now - activity).total_seconds() <= 900:
            current["state"] = "open"
    return sorted(grouped.values(), key=lambda item: (not item["local"], item["label"]))


def _merge_heartbeats(items: list[dict], heartbeats: list[dict]) -> list[dict]:
    merged = {str(item["id"]).casefold(): item for item in items}
    local_ids = {key for key, value in merged.items() if value.get("local")}
    for heartbeat in heartbeats:
        key = str(heartbeat["id"]).casefold()
        previous = merged.get(key, {})
        merged[key] = {
            **previous,
            **heartbeat,
            "local": key in local_ids or bool(previous.get("local")),
        }
    return sorted(merged.values(), key=lambda item: (not item.get("local"), item["label"]))


async def handler(request: web.Request) -> web.Response:
    user_id = int(request["user"]["id"])
    owner = config.ADMIN_ID is not None and user_id == config.ADMIN_ID
    degraded: list[str] = []
    accounts = _safe(lambda: repo.list_cli_accounts(user_id), [], what="accounts")
    recent = _safe(lambda: repo.list_conversations(user_id, limit=1), [], what="conversations")
    cwd = recent[0].get("cwd") if recent else config.user_default_cwd(user_id)
    try:
        workspace = await asyncio.to_thread(workspace_overview, user_id, cwd)
    except (sqlite3.Error, OSError) as error:
        log.warning("дашборд: не удалось собрать состояние проекта: %s", error)
        workspace = {}
        degraded.append("workspace")
    active_task = _safe(lambda: repo.get_active_task(user_id), None, what="active_task")
    crm_payload: object = []
    crm_error: str | None = None
    if owner and herecrm_client.configured():
        try:
            crm_payload = await asyncio.wait_for(herecrm_client.conversations(), timeout=4)
        except asyncio.TimeoutError:
            crm_error = "crm_unavailable"
        except herecrm_client.HereCrmClientError as error:
            crm_error = error.code
    contour_items = _contours(
        installation_identity(),
        _crm_sessions(crm_payload),
        local_working=active_task is not None,
    )
    contour_items = _merge_heartbeats(
        contour_items,
        _safe(lambda: contour_store.list_for_user(user_id), [], what="heartbeats"),
    )
    if crm_error:
        degraded.append("crm")
    return web.json_response(
        {
            # Пустой список означает, что показаны полные данные; непустой —
            # какие разделы не удалось получить, чтобы фронт не выдавал провал
            # за отсутствие данных.
            "degraded": degraded,
            "telegram": {
                "status": "active" if config.TELEGRAM_TOKEN else "not_configured",
                "user": request["user"],
            },
            "cli": {
                "status": "active" if accounts else "not_configured",
                "accounts": accounts,
                "launchCommand": "python chat.py",
            },
            "crm": {
                "status": "active" if owner and herecrm_client.configured() else "not_configured",
                "ownerOnly": True,
                "error": crm_error,
                "taskAutomation": "active" if config.HERECRM_MCP_CONFIGURED else "not_configured",
            },
            "workspace": workspace,
            "contours": contour_items,
        }
    )
