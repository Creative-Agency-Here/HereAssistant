"""Bounded HTTPS client for the public-client Gitea OAuth2/PKCE flow."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout

from core.git_connections import RepositoryMetadata
from core.git_oauth import REQUESTED_SCOPES

MAX_RESPONSE_BYTES = 262_144


class GiteaOAuthClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class GiteaIdentity:
    external_user_id: str
    login: str
    avatar_url: str | None
    access_token: str
    scopes: tuple[str, ...]
    expires_at: int | None
    repositories: tuple[RepositoryMetadata, ...]


def _repository(value: object) -> RepositoryMetadata:
    if not isinstance(value, dict):
        raise GiteaOAuthClientError("Gitea repository невалиден")
    owner = value.get("owner")
    permissions = value.get("permissions")
    owner_name = owner.get("login") if isinstance(owner, dict) else None
    if not isinstance(owner_name, str):
        owner_name = value.get("owner_name")
    external_id = value.get("id")
    name = value.get("name")
    clone_url = value.get("clone_url")
    if (
        external_id is None
        or not isinstance(owner_name, str)
        or not isinstance(name, str)
        or not isinstance(clone_url, str)
    ):
        raise GiteaOAuthClientError("Gitea repository невалиден")
    permission = "read"
    if isinstance(permissions, dict):
        if permissions.get("admin"):
            permission = "admin"
        elif permissions.get("push"):
            permission = "write"
    return RepositoryMetadata(
        external_repository_id=str(external_id),
        owner_name=owner_name,
        repository_name=name,
        clone_url=clone_url,
        default_branch=(str(value["default_branch"]) if value.get("default_branch") else None),
        permission=permission,
    )


async def _json_response(response) -> object:
    payload = await response.content.read(MAX_RESPONSE_BYTES + 1)
    if response.status < 200 or response.status >= 300 or len(payload) > MAX_RESPONSE_BYTES:
        raise GiteaOAuthClientError("Gitea response отклонён")
    try:
        value = json.loads(payload)
    except (UnicodeError, ValueError) as error:
        raise GiteaOAuthClientError("Gitea response невалиден") from error
    return value


async def exchange_code(
    host: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> GiteaIdentity:
    if not code or len(code) > 4096:
        raise GiteaOAuthClientError("OAuth code невалиден")
    base_url = f"https://{host}"
    timeout = ClientTimeout(total=20, connect=5)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{base_url}/login/oauth/access_token",
                json={
                    "client_id": client_id,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                allow_redirects=False,
            ) as response:
                token_payload = await _json_response(response)
            if not isinstance(token_payload, dict):
                raise GiteaOAuthClientError("Gitea token response невалиден")
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token or len(access_token) > 16_384:
                raise GiteaOAuthClientError("Gitea token невалиден")
            async with session.get(
                f"{base_url}/api/v1/user",
                headers={"Authorization": f"Bearer {access_token}"},
                allow_redirects=False,
            ) as response:
                user_payload = await _json_response(response)
            if not isinstance(user_payload, dict):
                raise GiteaOAuthClientError("Gitea user response невалиден")
            repositories: list[RepositoryMetadata] = []
            for page in range(1, 21):
                async with session.get(
                    f"{base_url}/api/v1/user/repos",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"limit": "50", "page": str(page)},
                    allow_redirects=False,
                ) as response:
                    repository_payload = await _json_response(response)
                raw_items = repository_payload
                if isinstance(repository_payload, dict):
                    raw_items = repository_payload.get("data")
                    if raw_items is None:
                        raw_items = repository_payload.get("repositories")
                if not isinstance(raw_items, list):
                    raise GiteaOAuthClientError("Gitea repositories невалидны")
                repositories.extend(_repository(value) for value in raw_items)
                if len(raw_items) < 50:
                    break
    except (ClientError, TimeoutError) as error:
        raise GiteaOAuthClientError("Gitea OAuth недоступен") from error
    external_id = user_payload.get("id")
    login = user_payload.get("login") or user_payload.get("username")
    if external_id is None or not isinstance(login, str) or not login or len(login) > 255:
        raise GiteaOAuthClientError("Gitea user невалиден")
    avatar = user_payload.get("avatar_url")
    expires_in = token_payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)) and 0 < int(expires_in) <= 31_536_000:
        expires_at = int(time.time()) + int(expires_in)
    raw_scope = token_payload.get("scope")
    scopes = (
        tuple(sorted(set(raw_scope.split())))
        if isinstance(raw_scope, str) and raw_scope.strip()
        else REQUESTED_SCOPES
    )
    return GiteaIdentity(
        external_user_id=str(external_id)[:255],
        login=login,
        avatar_url=str(avatar)[:1000] if isinstance(avatar, str) and avatar else None,
        access_token=access_token,
        scopes=scopes,
        expires_at=expires_at,
        repositories=tuple(repositories),
    )
