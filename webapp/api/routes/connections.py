"""Безопасная витрина каналов HereAssistant без credentials и путей auth-home."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from aiohttp import web

from core import config, herecrm_client
from core.workspace_status import installation_identity, parse_activity_at, workspace_overview
from webapp.api import repo


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


async def handler(request: web.Request) -> web.Response:
    user_id = int(request["user"]["id"])
    owner = config.ADMIN_ID is not None and user_id == config.ADMIN_ID
    accounts = repo.list_cli_accounts(user_id)
    recent = repo.list_conversations(user_id, limit=1)
    cwd = recent[0].get("cwd") if recent else config.user_default_cwd(user_id)
    workspace = workspace_overview(user_id, cwd)
    active_task = repo.get_active_task(user_id)
    crm_payload: object = []
    crm_error: str | None = None
    if owner and herecrm_client.configured():
        try:
            crm_payload = await asyncio.wait_for(herecrm_client.conversations(), timeout=4)
        except asyncio.TimeoutError:
            crm_error = "crm_unavailable"
        except herecrm_client.HereCrmClientError as error:
            crm_error = error.code
    contours = _contours(
        installation_identity(),
        _crm_sessions(crm_payload),
        local_working=active_task is not None,
    )
    return web.json_response(
        {
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
            },
            "workspace": workspace,
            "contours": contours,
        }
    )
