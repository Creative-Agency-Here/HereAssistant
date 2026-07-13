from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer

from core import config, db, git_connections
from core.git_connections import RepositoryMetadata
from webapp.api import server
from webapp.api.gitea_oauth import GiteaIdentity
from webapp.api.routes import git_connections as routes


def configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    monkeypatch.setattr(config, "GIT_ALLOWED_HOSTS", ("git.example.com",))
    monkeypatch.setattr(config, "GITEA_OAUTH_APPS", {"git.example.com": "public-client"})
    monkeypatch.setattr(config, "GIT_OAUTH_STATE_SECRET", "s" * 48)
    monkeypatch.setattr(config, "WEBAPP_URL", "https://assistant.example/app")
    monkeypatch.setattr(server, "DEV_SKIP_AUTH", True)
    db.init()


async def test_gitea_connection_flow_is_owner_scoped_and_token_free_in_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(tmp_path, monkeypatch)
    git_connections.create_pending_connection(200, "gitea", "git.example.com")
    vault_calls: list[tuple[int, int, str | None, str | None]] = []

    async def exchange(
        host: str, client_id: str, redirect_uri: str, code: str, verifier: str
    ) -> GiteaIdentity:
        assert host == "git.example.com"
        assert client_id == "public-client"
        assert redirect_uri == "https://assistant.example/api/git/oauth/callback/gitea"
        assert code == "one-time-code"
        assert 43 <= len(verifier) <= 128
        return GiteaIdentity(
            external_user_id="42",
            login="alice",
            avatar_url=None,
            access_token="runtime-oauth-token",
            scopes=("read:user", "write:repository"),
            expires_at=None,
            repositories=(
                RepositoryMetadata(
                    external_repository_id="77",
                    owner_name="alice",
                    repository_name="project",
                    clone_url="https://git.example.com/alice/project.git",
                    default_branch="main",
                    permission="write",
                ),
            ),
        )

    async def update(
        user_id: int,
        connection_id: int,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        vault_calls.append((user_id, connection_id, username, password))

    monkeypatch.setattr(routes, "exchange_code", exchange)
    monkeypatch.setattr(routes.git_vault_client, "update_credential", update)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        started = await client.post(
            "/api/git/connections/start",
            json={"provider": "gitea", "host": "git.example.com", "user_id": 999},
        )
        assert started.status == 201
        started_payload = await started.json()
        state = parse_qs(urlsplit(started_payload["authorization_url"]).query)["state"][0]

        callback = await client.get(
            "/api/git/oauth/callback/gitea",
            params={"state": state, "code": "one-time-code"},
            allow_redirects=False,
        )
        assert callback.status == 302
        assert callback.headers["Location"] == "https://assistant.example/app?git=connected"
        assert vault_calls == [
            (100, started_payload["connection_id"], "alice", "runtime-oauth-token")
        ]

        listed = await client.get("/api/git/connections")
        listed_payload = await listed.json()
        assert len(listed_payload["connections"]) == 1
        assert listed_payload["connections"][0]["external_login"] == "alice"
        assert listed_payload["connections"][0]["status"] == "active"
        assert "vault_ref" not in listed_payload["connections"][0]
        assert b"runtime-oauth-token" not in config.DB_PATH.read_bytes()

        repositories = await client.get(
            f"/api/git/connections/{started_payload['connection_id']}/repositories"
        )
        repository_payload = await repositories.json()
        assert repository_payload["repositories"][0]["enabled"] is False
        granted = await client.post(
            f"/api/git/connections/{started_payload['connection_id']}/repositories/77/grant"
        )
        assert granted.status == 200
        assert (await granted.json())["enabled"] is True
        denied_foreign = await client.post(
            f"/api/git/connections/{started_payload['connection_id']}/repositories/999/grant"
        )
        assert denied_foreign.status == 404

        replay = await client.get(
            "/api/git/oauth/callback/gitea",
            params={"state": state, "code": "one-time-code"},
            allow_redirects=False,
        )
        assert replay.headers["Location"] == "https://assistant.example/app?git=error"
        assert len(vault_calls) == 1

        revoked = await client.delete(f"/api/git/connections/{started_payload['connection_id']}")
        assert revoked.status == 204
        assert vault_calls[-1] == (100, started_payload["connection_id"], None, None)
    finally:
        await client.close()


async def test_start_fails_closed_for_unconfigured_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "GITEA_OAUTH_APPS", {})
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            "/api/git/connections/start",
            json={"provider": "gitea", "host": "git.example.com"},
        )
        assert response.status == 503
        with db.conn() as connection:
            assert connection.execute("SELECT COUNT(*) FROM git_connections").fetchone()[0] == 0
    finally:
        await client.close()
