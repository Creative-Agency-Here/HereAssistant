"""Браузерный SSO HereCRM → HereAssistant."""

from __future__ import annotations

from urllib.parse import urlparse

from aiohttp import web

from core import config, herecrm_client
from webapp.api import browser_session


async def exchange_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "bad_ticket"}, status=400)
    ticket = str(body.get("ticket", "")) if isinstance(body, dict) else ""
    if not ticket.startswith("hat_") or len(ticket) != 68:
        return web.json_response({"error": "bad_ticket"}, status=400)
    try:
        identity = await herecrm_client.exchange_sso_ticket(ticket)
        session = browser_session.issue(
            crm_user_id=int(identity["userId"]),
            tenant_id=str(identity["tenantId"]),
        )
    except herecrm_client.HereCrmClientError as error:
        return web.json_response({"error": error.code}, status=error.status)
    except (KeyError, TypeError, ValueError, RuntimeError):
        return web.json_response({"error": "crm_sso_invalid_response"}, status=502)

    response = web.json_response({"ok": True})
    response.set_cookie(
        browser_session.COOKIE_NAME,
        session,
        max_age=browser_session.SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


async def session_handler(request: web.Request) -> web.Response:
    user = request["user"]
    return web.json_response(
        {
            "authenticated": True,
            "source": user.get("auth_source", "telegram"),
        }
    )


async def config_handler(_request: web.Request) -> web.Response:
    """Возвращает только публичные URL; server-side sync-токен не раскрывается."""
    parsed = urlparse(config.HERECRM_SYNC_URL)
    derived_web_url = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme == "https" and parsed.netloc
        else ""
    )
    return web.json_response(
        {
            "crmApiBase": config.HERECRM_SYNC_URL,
            "crmWebUrl": config.HERECRM_WEB_URL or derived_web_url,
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )
