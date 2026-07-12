import sqlite3
from pathlib import Path

import pytest

from core import config, db, git_connections
from core.git_projects import GitRemoteDeniedError


@pytest.fixture
def connection_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / ".runtime"
    for name, value in {
        "RUNTIME_DIR": runtime,
        "DOWNLOADS_DIR": runtime / "downloads",
        "LOGS_DIR": runtime / "logs",
        "BACKUPS_DIR": runtime / "backups",
        "STATE_DIR": runtime / "state",
        "CLI_HOMES_DIR": runtime / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(config, "GIT_ALLOWED_HOSTS", ("github.com", "git.example.com"))
    db.init()
    return config.DB_PATH


def test_connection_metadata_is_owner_isolated_and_hides_vault_ref(connection_db: Path) -> None:
    alice = git_connections.create_pending_connection(100, "github", "github.com")
    bob = git_connections.create_pending_connection(200, "github", "github.com")

    activated = git_connections.activate_connection(
        100,
        alice["id"],
        external_user_id="alice-id",
        external_login="alice",
        avatar_url="https://avatars.example/alice",
        vault_ref=f"vault://git/100/{alice['id']}/primary",
        scopes=["contents:write", "contents:write", "metadata:read"],
        expires_at=123456,
    )

    assert activated is not None
    assert activated["status"] == "active"
    assert activated["scopes_json"] == '["contents:write","metadata:read"]'
    assert "vault_ref" not in activated.keys()
    assert git_connections.get_connection(200, alice["id"]) is None
    assert (
        git_connections.activate_connection(
            200,
            alice["id"],
            external_user_id="foreign",
            external_login="foreign",
            avatar_url=None,
            vault_ref=f"vault://git/200/{alice['id']}/foreign",
            scopes=[],
            expires_at=None,
        )
        is None
    )
    assert [row["id"] for row in git_connections.list_connections(100)] == [alice["id"]]
    assert [row["id"] for row in git_connections.list_connections(200)] == [bob["id"]]


def test_repository_grants_require_active_owned_connection(connection_db: Path) -> None:
    current = git_connections.create_pending_connection(100, "github", "github.com")
    with pytest.raises(git_connections.GitConnectionError, match="не активен"):
        git_connections.grant_repository(
            100,
            current["id"],
            external_repository_id="repo-1",
            owner_name="alice",
            repository_name="project",
            clone_url="https://github.com/alice/project.git",
            default_branch="main",
            permission="write",
        )

    git_connections.activate_connection(
        100,
        current["id"],
        external_user_id="alice-id",
        external_login="alice",
        avatar_url=None,
        vault_ref=f"vault://git/100/{current['id']}/primary",
        scopes=["contents:write"],
        expires_at=None,
    )
    grant = git_connections.grant_repository(
        100,
        current["id"],
        external_repository_id="repo-1",
        owner_name="alice",
        repository_name="project",
        clone_url="https://github.com/alice/project.git",
        default_branch="main",
        permission="write",
    )

    assert grant["enabled"] == 1
    assert [row["id"] for row in git_connections.list_repository_grants(100)] == [grant["id"]]
    assert git_connections.list_repository_grants(200) == []
    with pytest.raises(git_connections.GitConnectionError, match="недоступен"):
        git_connections.grant_repository(
            200,
            current["id"],
            external_repository_id="repo-1",
            owner_name="alice",
            repository_name="project",
            clone_url="https://github.com/alice/project.git",
            default_branch="main",
            permission="write",
        )

    assert not git_connections.revoke_connection(200, current["id"])
    assert git_connections.revoke_connection(100, current["id"])
    revoked = git_connections.get_connection(100, current["id"])
    assert revoked is not None
    assert revoked["status"] == "revoked"
    assert git_connections.list_repository_grants(100)[0]["enabled"] == 0
    with sqlite3.connect(connection_db) as connection:
        vault_ref = connection.execute(
            "SELECT vault_ref FROM git_connections WHERE id=?", (current["id"],)
        ).fetchone()[0]
    assert vault_ref is None


def test_connection_rejects_unapproved_host_and_raw_secret_reference(connection_db: Path) -> None:
    with pytest.raises(GitRemoteDeniedError):
        git_connections.create_pending_connection(100, "gitea", "evil.example")

    current = git_connections.create_pending_connection(100, "github", "github.com")
    with pytest.raises(git_connections.GitConnectionError, match="opaque reference"):
        git_connections.activate_connection(
            100,
            current["id"],
            external_user_id="alice-id",
            external_login="alice",
            avatar_url=None,
            vault_ref="raw-secret-value",
            scopes=[],
            expires_at=None,
        )
