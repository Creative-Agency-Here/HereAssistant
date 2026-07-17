"""Безопасная витрина каналов HereAssistant без credentials и путей auth-home."""

from __future__ import annotations

from aiohttp import web

from core import config, herecrm_client
from webapp.api import repo


async def handler(request: web.Request) -> web.Response:
    user_id = int(request["user"]["id"])
    owner = config.ADMIN_ID is not None and user_id == config.ADMIN_ID
    accounts = repo.list_cli_accounts(user_id)
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
            },
        }
    )
