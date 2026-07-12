import json
import sqlite3
from pathlib import Path

import pytest

from runner.git_vault_admin import (
    GitVaultAdminError,
    connection_vault_ref,
    parse_secret_request,
    rotate_bundle,
)
from runner.git_vault_service import VaultCredential


def create_connections_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE git_connections (
                id INTEGER PRIMARY KEY, user_id INTEGER, vault_ref TEXT
            );
            INSERT INTO git_connections VALUES
                (1,100,NULL),
                (2,200,'vault://git/200/2/primary'),
                (3,100,'vault://git/100/3/unexpected');
            """
        )


def test_secret_request_is_strict_and_bounded() -> None:
    credential = parse_secret_request(
        "put", json.dumps({"username": "oauth-user", "password": "runtime-token"}).encode()
    )

    assert credential == VaultCredential("oauth-user", "runtime-token")
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
