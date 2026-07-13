import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from runner.git_vault_admin import (
    GitVaultAdminError,
    StoredCredential,
    _reload_service,
    connection_refresh_target,
    connection_vault_ref,
    parse_secret_request,
    refresh_bundle,
    rotate_bundle,
)
from runner.git_vault_service import VaultCredential


def create_connections_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE git_connections (
                id INTEGER PRIMARY KEY, user_id INTEGER, vault_ref TEXT,
                provider TEXT, host TEXT, status TEXT
            );
            INSERT INTO git_connections VALUES
                (1,100,NULL,'gitea','git.example.com','expired'),
                (2,200,'vault://git/200/2/primary','gitea','git.example.com','active'),
                (3,100,'vault://git/100/3/unexpected','gitea','git.example.com','revoked');
            """
        )


def test_secret_request_is_strict_and_bounded() -> None:
    credential = parse_secret_request(
        "put", json.dumps({"username": "oauth-user", "password": "runtime-token"}).encode()
    )

    assert credential == StoredCredential("oauth-user", "runtime-token")
    with_refresh = parse_secret_request(
        "put",
        b'{"username":"oauth-user","password":"access","refresh_token":"refresh"}',
    )
    assert with_refresh == StoredCredential("oauth-user", "access", "refresh")
    assert parse_secret_request("revoke", b"") is None
    with pytest.raises(GitVaultAdminError):
        parse_secret_request("put", b'{"username":"user","password":"token","extra":1}')
    with pytest.raises(GitVaultAdminError):
        parse_secret_request("put", b'{"username":"user","password":"bad\\nvalue"}')


def test_connection_reference_is_bound_to_configured_owner(tmp_path: Path) -> None:
    database = tmp_path / "bridge.sqlite3"
    create_connections_database(database)

    assert connection_vault_ref(database, 100, 1) == "vault://git/100/1/primary"
    with pytest.raises(GitVaultAdminError, match="unavailable"):
        connection_vault_ref(database, 100, 2)
    with pytest.raises(GitVaultAdminError, match="mismatch"):
        connection_vault_ref(database, 100, 3)
    assert connection_refresh_target(database, 100, 1) == ("gitea", "git.example.com")
    with pytest.raises(GitVaultAdminError, match="unavailable"):
        connection_refresh_target(database, 100, 3)


def test_rotation_uses_stdin_not_argv_and_replaces_atomically(tmp_path: Path) -> None:
    encrypted = tmp_path / "ha-ilya-git.json.cred"
    encrypted.write_bytes(b"encrypted-old")
    commands: list[tuple[list[str], bytes | None]] = []

    def fake_systemd_creds(command: list[str], payload: bytes | None) -> bytes:
        commands.append((command, payload))
        if command[1] == "decrypt":
            return json.dumps(
                {
                    "vault://git/100/9/primary": {
                        "username": "old-user",
                        "password": "old-token",
                    }
                }
            ).encode()
        assert payload is not None
        return b"encrypted-new"

    rotate_bundle(
        encrypted,
        "vault://git/100/1/primary",
        VaultCredential("oauth-user", "runtime-token"),
        command_runner=fake_systemd_creds,
    )

    assert encrypted.read_bytes() == b"encrypted-new"
    argv = " ".join(item for command, _payload in commands for item in command)
    assert "oauth-user" not in argv
    assert "runtime-token" not in argv
    plaintext = json.loads(commands[-1][1] or b"{}")
    assert plaintext["vault://git/100/1/primary"]["password"] == "runtime-token"
    assert not list(tmp_path.glob(".ha-ilya-git.json.cred.*"))


def test_failed_encryption_preserves_previous_bundle(tmp_path: Path) -> None:
    encrypted = tmp_path / "ha-ilya-git.json.cred"
    encrypted.write_bytes(b"encrypted-old")

    def failing_systemd_creds(command: list[str], _payload: bytes | None) -> bytes:
        if command[1] == "decrypt":
            return b"{}"
        raise GitVaultAdminError("encrypt failed")

    with pytest.raises(GitVaultAdminError, match="encrypt failed"):
        rotate_bundle(
            encrypted,
            "vault://git/100/1/primary",
            VaultCredential("oauth-user", "runtime-token"),
            command_runner=failing_systemd_creds,
        )

    assert encrypted.read_bytes() == b"encrypted-old"
    assert not list(tmp_path.glob(".ha-ilya-git.json.cred.*"))


def test_revoke_removes_only_selected_reference(tmp_path: Path) -> None:
    encrypted = tmp_path / "ha-ilya-git.json.cred"
    encrypted.write_bytes(b"encrypted-old")
    encrypted_plaintext: bytes | None = None

    def fake_systemd_creds(command: list[str], payload: bytes | None) -> bytes:
        nonlocal encrypted_plaintext
        if command[1] == "decrypt":
            return json.dumps(
                {
                    "vault://git/100/1/primary": {"username": "one", "password": "first"},
                    "vault://git/100/2/primary": {"username": "two", "password": "second"},
                }
            ).encode()
        encrypted_plaintext = payload
        return b"encrypted-new"

    rotate_bundle(
        encrypted,
        "vault://git/100/1/primary",
        None,
        command_runner=fake_systemd_creds,
    )

    result = json.loads(encrypted_plaintext or b"{}")
    assert "vault://git/100/1/primary" not in result
    assert result["vault://git/100/2/primary"]["username"] == "two"


def test_refresh_rotates_tokens_inside_encrypted_bundle(tmp_path: Path) -> None:
    encrypted = tmp_path / "ha-ilya-git.json.cred"
    encrypted.write_bytes(b"encrypted-old")
    encrypted_plaintext: bytes | None = None

    def fake_systemd_creds(command: list[str], payload: bytes | None) -> bytes:
        nonlocal encrypted_plaintext
        if command[1] == "decrypt":
            return json.dumps(
                {
                    "vault://git/100/1/primary": {
                        "username": "alice",
                        "password": "old-access",
                        "refresh_token": "old-refresh",
                    }
                }
            ).encode()
        encrypted_plaintext = payload
        return b"encrypted-new"

    def exchange(host: str, client_id: str, refresh_token: str) -> tuple[str, str, int]:
        assert (host, client_id, refresh_token) == (
            "git.example.com",
            "public-client",
            "old-refresh",
        )
        return "new-access", "new-refresh", 2_000_000_000

    expires_at = refresh_bundle(
        encrypted,
        "vault://git/100/1/primary",
        "git.example.com",
        "public-client",
        command_runner=fake_systemd_creds,
        token_exchange=exchange,
    )

    result = json.loads(encrypted_plaintext or b"{}")
    credential = result["vault://git/100/1/primary"]
    assert expires_at == 2_000_000_000
    assert credential["password"] == "new-access"
    assert credential["refresh_token"] == "new-refresh"
    assert encrypted.read_bytes() == b"encrypted-new"


def test_refresh_without_refresh_token_preserves_bundle(tmp_path: Path) -> None:
    encrypted = tmp_path / "ha-ilya-git.json.cred"
    encrypted.write_bytes(b"encrypted-old")

    def fake_systemd_creds(command: list[str], _payload: bytes | None) -> bytes:
        assert command[1] == "decrypt"
        return b'{"vault://git/100/1/primary":{"username":"alice","password":"access"}}'

    with pytest.raises(GitVaultAdminError, match="refresh credential unavailable"):
        refresh_bundle(
            encrypted,
            "vault://git/100/1/primary",
            "git.example.com",
            "public-client",
            command_runner=fake_systemd_creds,
        )

    assert encrypted.read_bytes() == b"encrypted-old"


@pytest.mark.parametrize(
    ("ensure_started", "expected_action"),
    ((True, ["enable", "--now"]), (False, ["try-restart"])),
)
def test_vault_service_start_policy(
    monkeypatch: pytest.MonkeyPatch,
    ensure_started: bool,
    expected_action: list[str],
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("runner.git_vault_admin.subprocess.run", fake_run)

    _reload_service("ha-user-git", ensure_started=ensure_started)

    assert commands == [
        ["systemctl", *expected_action, "hereassistant-git-vault@ha-user-git.service"]
    ]
