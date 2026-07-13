"""Owner-scoped WebApp endpoints for Gitea OAuth connections."""

from __future__ import annotations

import logging
from urllib.parse import urlencode, urlsplit, urlunsplit

from aiohttp import web

from core import config, git_connections, git_oauth, git_vault_client
from webapp.api.gitea_oauth import GiteaOAuthClientError, exchange_code
from webapp.api.models import (
    git_connection_to_dto,
    parse_git_connection_start,
    parse_git_repository_bulk_grant,
)

MAX_JSON_BYTES = 16_384
log = logging.getLogger(__name__)


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
    base_path = (parsed.path or "/").rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, f"{base_path}/settings", query, ""))


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
    connection_activated = False
    stage = "claim"
    try:
        claim = git_oauth.claim_gitea_callback(state, config.GIT_OAUTH_STATE_SECRET)
        if request.query.get("error") or claim.provider != "gitea":
            raise git_oauth.GitOAuthError("Gitea authorization отклонена")
        client_id = config.GITEA_OAUTH_APPS.get(claim.host)
        if not client_id or claim.host not in config.GIT_ALLOWED_HOSTS:
            raise git_oauth.GitOAuthError("Gitea OAuth app не настроен")
        stage = "exchange"
        identity = await exchange_code(
            claim.host,
            client_id,
            _callback_uri(),
            code,
            claim.verifier,
        )
        stage = "vault"
        await git_vault_client.update_credential(
            claim.user_id,
            claim.connection_id,
            username=identity.login,
            password=identity.access_token,
            refresh_token=identity.refresh_token,
        )
        vault_written = True
        stage = "activation"
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
        connection_activated = True
        stage = "repository_sync"
        git_connections.sync_repository_catalog(
            claim.user_id, claim.connection_id, identity.repositories
        )
    except (
        GiteaOAuthClientError,
        git_connections.GitConnectionError,
        git_oauth.GitOAuthError,
        git_vault_client.GitVaultClientError,
    ) as error:
        provider_stage = getattr(error, "stage", "none")
        provider_status = getattr(error, "status", None)
        provider_reason = getattr(error, "reason", "none")
        log.warning(
            "Gitea OAuth callback отклонён: "
            "stage=%s provider_stage=%s status=%s reason=%s error=%s",
            stage,
            provider_stage,
            provider_status if provider_status is not None else "none",
            provider_reason,
            type(error).__name__,
        )
        if claim is not None:
            git_oauth.mark_callback_failed(claim.session_id)
            if connection_activated:
                git_connections.revoke_connection(claim.user_id, claim.connection_id)
            else:
                git_connections.mark_connection_failed(claim.user_id, claim.connection_id)
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


async def repositories_handler(request: web.Request) -> web.Response:
    try:
        connection_id = int(request.match_info["connection_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid connection"}, status=400)
    if git_connections.get_connection(_user_id(request), connection_id) is None:
        return web.json_response({"error": "connection not found"}, status=404)
    rows = git_connections.list_repository_grants(_user_id(request), connection_id)
    return web.json_response(
        {
            "repositories": [
                {
                    "external_repository_id": str(row["external_repository_id"]),
                    "owner_name": str(row["owner_name"]),
                    "repository_name": str(row["repository_name"]),
                    "clone_url": str(row["clone_url"]),
                    "default_branch": row["default_branch"],
                    "permission": str(row["permission"]),
                    "enabled": bool(row["enabled"]),
                }
                for row in rows
            ]
        }
    )


async def repository_grant_handler(request: web.Request) -> web.Response:
    try:
        connection_id = int(request.match_info["connection_id"])
        repository_id = request.match_info["repository_id"]
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid repository"}, status=400)
    if not repository_id or len(repository_id) > 255:
        return web.json_response({"error": "invalid repository"}, status=400)
    enabled = request.method == "POST"
    try:
        row = git_connections.set_repository_enabled(
            _user_id(request), connection_id, repository_id, enabled
        )
    except git_connections.GitConnectionError:
        return web.json_response({"error": "invalid repository"}, status=400)
    if row is None:
        return web.json_response({"error": "repository not found"}, status=404)
    return web.json_response({"enabled": bool(row["enabled"])})


async def repository_bulk_grant_handler(request: web.Request) -> web.Response:
    try:
        connection_id = int(request.match_info["connection_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid connection"}, status=400)
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return web.json_response({"error": "invalid payload"}, status=400)
    try:
        payload = await request.json()
    except (ValueError, web.HTTPException):
        return web.json_response({"error": "invalid payload"}, status=400)
    values = parse_git_repository_bulk_grant(payload)
    if values is None:
        return web.json_response({"error": "invalid payload"}, status=400)
    try:
        rows = git_connections.set_repositories_enabled(
            _user_id(request),
            connection_id,
            values["repository_ids"],
            values["enabled"],
        )
    except git_connections.GitConnectionError:
        return web.json_response({"error": "repositories unavailable"}, status=404)
    return web.json_response(
        {
            "updated": len(rows),
            "enabled": values["enabled"],
            "repository_ids": [str(row["external_repository_id"]) for row in rows],
        }
    )


async def refresh_handler(request: web.Request) -> web.Response:
    try:
        connection_id = int(request.match_info["connection_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid connection"}, status=400)
    user_id = _user_id(request)
    current = git_connections.get_connection(user_id, connection_id)
    if current is None:
        return web.json_response({"error": "connection not found"}, status=404)
    if current["provider"] != "gitea" or current["status"] not in ("active", "expired"):
        return web.json_response({"error": "connection not refreshable"}, status=409)
    try:
        expires_at = await git_vault_client.refresh_credential(user_id, connection_id)
        if not git_connections.mark_connection_refreshed(user_id, connection_id, expires_at):
            raise git_connections.GitConnectionError("Git connection недоступен")
    except (git_connections.GitConnectionError, git_vault_client.GitVaultClientError):
        return web.json_response({"error": "refresh unavailable"}, status=503)
    return web.json_response({"status": "active", "expires_at": expires_at})
