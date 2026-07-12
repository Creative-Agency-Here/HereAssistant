import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from core import config, db, git_connections, git_oauth


def configure_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "GIT_ALLOWED_HOSTS", ("git.example.com",))
    db.init()


def test_pkce_session_stores_only_hash_and_is_single_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_database(tmp_path, monkeypatch)
    connection = git_connections.create_pending_connection(100, "gitea", "git.example.com")
    monkeypatch.setattr(git_oauth.secrets, "token_urlsafe", lambda _size: "browser-state-value")

    started = git_oauth.start_gitea_oauth(
        100,
        int(connection["id"]),
        "git.example.com",
        "public-client-id",
        "https://assistant.example/api/git/oauth/callback/gitea",
        "s" * 48,
        now=1_000,
    )

    query = parse_qs(urlsplit(started.authorization_url).query)
    assert query["state"] == ["browser-state-value"]
    assert query["code_challenge_method"] == ["S256"]
    assert "browser-state-value" not in config.DB_PATH.read_bytes().decode(errors="ignore")
    with sqlite3.connect(config.DB_PATH) as database:
        state_hash, verifier_ref = database.execute(
            "SELECT state_hash,verifier_ref FROM git_auth_sessions"
        ).fetchone()
    assert state_hash != "browser-state-value"
    assert verifier_ref == git_oauth.VERIFIER_REFERENCE

    claim = git_oauth.claim_gitea_callback("browser-state-value", "s" * 48, now=1_001)

    assert claim.user_id == 100
    assert claim.connection_id == int(connection["id"])
    assert 43 <= len(claim.verifier) <= 128
    with pytest.raises(git_oauth.GitOAuthError, match="недоступна"):
        git_oauth.claim_gitea_callback("browser-state-value", "s" * 48, now=1_002)


def test_expired_or_wrong_state_cannot_claim_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_database(tmp_path, monkeypatch)
    connection = git_connections.create_pending_connection(200, "gitea", "git.example.com")
    monkeypatch.setattr(git_oauth.secrets, "token_urlsafe", lambda _size: "expiring-state")
    git_oauth.start_gitea_oauth(
        200,
        int(connection["id"]),
        "git.example.com",
        "client",
        "https://assistant.example/api/git/oauth/callback/gitea",
        "k" * 48,
        now=1_000,
    )

    with pytest.raises(git_oauth.GitOAuthError, match="недоступна"):
        git_oauth.claim_gitea_callback("foreign-state", "k" * 48, now=1_001)
    with pytest.raises(git_oauth.GitOAuthError, match="истекла"):
        git_oauth.claim_gitea_callback("expiring-state", "k" * 48, now=2_000)


def test_reconnect_moves_revoked_connection_back_to_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_database(tmp_path, monkeypatch)
    connection = git_connections.create_pending_connection(100, "gitea", "git.example.com")
    assert git_connections.revoke_connection(100, int(connection["id"]))

    restarted = git_connections.create_pending_connection(100, "gitea", "git.example.com")

    assert restarted["status"] == "pending"
