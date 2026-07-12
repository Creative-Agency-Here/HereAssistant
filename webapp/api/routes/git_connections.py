"""Owner-scoped WebApp endpoints for Gitea OAuth connections."""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit

from aiohttp import web

from core import config, git_connections, git_oauth, git_vault_client
from webapp.api.gitea_oauth import GiteaOAuthClientError, exchange_code
from webapp.api.models import git_connection_to_dto, parse_git_connection_start

MAX_JSON_BYTES = 16_384


def _user_id(request: web.Request) -> int:
    return int(request["user"]["id"])


def _callback_uri() -> str:
    parsed = urlsplit(config.WEBAPP_URL)
    if parsed.scheme != "https" or not parsed.netloc:
        raise git_oauth.GitOAuthError("WEBAPP_URL должен быть публичным HTTPS URL")
    return urlunsplit((parsed.scheme, parsed.netloc, "/api/git/oauth/callback/gitea", "", ""))


def _result_uri(result: str) -> str:
    parsed = urlsplit(config.WEBAPP_URL)
    query = urlencode({"git": result})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", query, ""))


async def list_handler(request: web.Request) -> web.Response:
    rows = git_connections.list_connections(_user_id(request))
    available = [
        {"provider": "gitea", "host": host}
        for host in sorted(config.GITEA_OAUTH_APPS)
        if host in config.GIT_ALLOWED_HOSTS
    ]
    return web.json_response(
        {
            "connections": [git_connection_to_dto(row) for row in rows],
            "available": available,
        }
    )


async def start_handler(request: web.Request) -> web.Response:
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return web.json_response({"error": "invalid payload"}, status=400)
    try:
        payload = await request.json()
    except (ValueError, web.HTTPException):
        return web.json_response({"error": "invalid payload"}, status=400)
    values = parse_git_connection_start(payload)
    if values is None:
        return web.json_response({"error": "invalid payload"}, status=400)
    if values["provider"] != "gitea":
        return web.json_response({"error": "provider not implemented"}, status=501)
    client_id = config.GITEA_OAUTH_APPS.get(values["host"])
    if not client_id or values["host"] not in config.GIT_ALLOWED_HOSTS:
        return web.json_response({"error": "oauth app not configured"}, status=503)
    user_id = _user_id(request)
    existing = next(
        (
            row
            for row in git_connections.list_connections(user_id)
            if row["provider"] == "gitea" and row["host"] == values["host"]
        ),
        None,
    )
    if existing is not None and existing["status"] == "active":
        return web.json_response({"error": "connection already active"}, status=409)
    try:
        connection = git_connections.create_pending_connection(user_id, "gitea", values["host"])
        oauth = git_oauth.start_gitea_oauth(
            user_id,
            int(connection["id"]),
            values["host"],
            client_id,
            _callback_uri(),
            config.GIT_OAUTH_STATE_SECRET,
        )
    except (git_connections.GitConnectionError, git_oauth.GitOAuthError):
        return web.json_response({"error": "oauth unavailable"}, status=503)
    return web.json_response(
        {"connection_id": oauth.connection_id, "authorization_url": oauth.authorization_url},
        status=201,
    )


async def callback_handler(request: web.Request) -> web.StreamResponse:
    state = request.query.get("state", "")
    code = request.query.get("code", "")
    claim: git_oauth.OAuthClaim | None = None
    vault_written = False
    try:
        claim = git_oauth.claim_gitea_callback(state, config.GIT_OAUTH_STATE_SECRET)
        if request.query.get("error") or claim.provider != "gitea":
            raise git_oauth.GitOAuthError("Gitea authorization отклонена")
        client_id = config.GITEA_OAUTH_APPS.get(claim.host)
        if not client_id or claim.host not in config.GIT_ALLOWED_HOSTS:
            raise git_oauth.GitOAuthError("Gitea OAuth app не настроен")
        identity = await exchange_code(
            claim.host,
            client_id,
            _callback_uri(),
            code,
            claim.verifier,
        )
        await git_vault_client.update_credential(
            claim.user_id,
            claim.connection_id,
            username=identity.login,
            password=identity.access_token,
        )
        vault_written = True
        activated = git_connections.activate_connection(
            claim.user_id,
            claim.connection_id,
            external_user_id=identity.external_user_id,
            external_login=identity.login,
            avatar_url=identity.avatar_url,
            vault_ref=f"vault://git/{claim.user_id}/{claim.connection_id}/primary",
            scopes=identity.scopes,
            expires_at=identity.expires_at,
        )
        if activated is None:
            raise git_oauth.GitOAuthError("Git connection недоступен")
    except (
        GiteaOAuthClientError,
        git_connections.GitConnectionError,
        git_oauth.GitOAuthError,
        git_vault_client.GitVaultClientError,
    ):
        if claim is not None:
            git_oauth.mark_callback_failed(claim.session_id)
            if vault_written:
                try:
                    await git_vault_client.update_credential(claim.user_id, claim.connection_id)
                except git_vault_client.GitVaultClientError:
                    pass
        raise web.HTTPFound(_result_uri("error"))
    raise web.HTTPFound(_result_uri("connected"))


async def revoke_handler(request: web.Request) -> web.Response:
    try:
        connection_id = int(request.match_info["connection_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid connection"}, status=400)
    user_id = _user_id(request)
    if not git_connections.revoke_connection(user_id, connection_id):
        return web.json_response({"error": "connection not found"}, status=404)
    try:
        await git_vault_client.update_credential(user_id, connection_id)
    except git_vault_client.GitVaultClientError:
        return web.json_response({"error": "credential cleanup pending"}, status=503)
    return web.Response(status=204)
