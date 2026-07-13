import json
import sqlite3
from pathlib import Path

import pytest

from runner.git_credential_proxy import CredentialRequest
from runner.git_vault_service import (
    GitVaultError,
    VaultCredential,
    load_credential_bundle,
    parse_socket_request,
    resolve_credential,
)


def create_grants_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE git_connections (
                id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, vault_ref TEXT,
                expires_at INTEGER
            );
            CREATE TABLE git_repository_grants (
                id INTEGER PRIMARY KEY, connection_id INTEGER, permission TEXT,
                clone_url TEXT, enabled INTEGER
            );
            INSERT INTO git_connections VALUES
                (1,100,'active','vault://git/100/1/primary',NULL),
                (2,200,'active','vault://git/200/2/foreign',NULL),
                (3,100,'active','vault://git/100/3/expired',1);
            INSERT INTO git_repository_grants VALUES
                (1,1,'write','https://git.example.com/alice/project.git',1),
                (2,1,'read','https://git.example.com/alice/read-only.git',1),
                (3,2,'write','https://git.example.com/bob/private.git',1),
                (4,3,'write','https://git.example.com/alice/expired.git',1);
            """
        )


def test_vault_resolves_only_owned_granted_repository_and_permission(tmp_path: Path) -> None:
    database = tmp_path / "bridge.sqlite3"
    create_grants_database(database)
    credentials = {
        "vault://git/100/1/primary": VaultCredential("oauth-user", "runtime-value"),
        "vault://git/200/2/foreign": VaultCredential("foreign", "foreign-value"),
    }

    result = resolve_credential(
        database,
        100,
        CredentialRequest("get", "write", "https", "git.example.com", "alice/project.git"),
        credentials,
    )

    assert result == VaultCredential("oauth-user", "runtime-value")
    with pytest.raises(GitVaultError, match="write grant"):
        resolve_credential(
            database,
            100,
            CredentialRequest("get", "write", "https", "git.example.com", "alice/read-only.git"),
            credentials,
        )
    with pytest.raises(GitVaultError, match="grant отсутствует"):
        resolve_credential(
            database,
            100,
            CredentialRequest("get", "read", "https", "git.example.com", "bob/private.git"),
            credentials,
        )
    with pytest.raises(GitVaultError, match="grant отсутствует"):
        resolve_credential(
            database,
            100,
            CredentialRequest("get", "write", "https", "git.example.com", "alice/expired.git"),
            {**credentials, "vault://git/100/3/expired": VaultCredential("old", "expired")},
        )


def test_vault_bundle_and_socket_request_are_strict(tmp_path: Path) -> None:
    bundle = tmp_path / "git-credentials.json"
    bundle.write_text(
        json.dumps(
            {
                "vault://git/100/1/primary": {
                    "username": "oauth-user",
                    "password": "runtime-value",
                }
            }
        ),
        encoding="utf-8",
    )
    bundle.chmod(0o600)

    loaded = load_credential_bundle(tmp_path)
    request = parse_socket_request(
        b'{"operation":"get","access":"read","protocol":"https",'
        b'"host":"git.example.com","path":"alice/project.git"}\n'
    )

    assert loaded["vault://git/100/1/primary"].username == "oauth-user"
    assert request.access == "read"
    with pytest.raises(GitVaultError):
        parse_socket_request(
            b'{"operation":"get","access":"admin","protocol":"https",'
            b'"host":"git.example.com","path":"alice/project.git"}\n'
        )
