"""Owner-isolated metadata for Git identities and repository grants.

Raw credentials belong to a separate Git broker vault. This module accepts and
returns only public metadata plus opaque vault references.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass

from . import config, db
from .git_projects import GitRemoteDeniedError, validate_repository_url

HOST_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?$")
VAULT_REF_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://[A-Za-z0-9._/-]+$")
PROVIDERS = frozenset({"gitea", "github"})
PERMISSIONS = frozenset({"read", "write", "admin"})


class GitConnectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepositoryMetadata:
    external_repository_id: str
    owner_name: str
    repository_name: str
    clone_url: str
    default_branch: str | None
    permission: str


def normalize_host(host: str) -> str:
    value = host.strip().lower()
    if not HOST_PATTERN.fullmatch(value):
        raise GitConnectionError("Некорректный Git host")
    return value


def normalize_provider(provider: str) -> str:
    value = provider.strip().lower()
    if value not in PROVIDERS:
        raise GitConnectionError("Git provider не поддерживается")
    return value


def validate_vault_ref(vault_ref: str, user_id: int, connection_id: int) -> str:
    value = vault_ref.strip()
    expected_prefix = f"vault://git/{user_id}/{connection_id}/"
    if not VAULT_REF_PATTERN.fullmatch(value) or not value.startswith(expected_prefix):
        raise GitConnectionError("vault_ref должен быть opaque reference без credentials")
    return value


def _required_metadata(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise GitConnectionError(f"{field} обязателен")
    return normalized[:255]


def _allowed_host(host: str) -> None:
    allowed = {value.lower() for value in config.GIT_ALLOWED_HOSTS}
    hostname = host.rsplit(":", 1)[0] if ":" in host else host
    if host not in allowed and hostname not in allowed:
        raise GitRemoteDeniedError("Git host не входит в GIT_ALLOWED_HOSTS")


def create_pending_connection(user_id: int, provider: str, host: str) -> sqlite3.Row:
    provider = normalize_provider(provider)
    host = normalize_host(host)
    _allowed_host(host)
    now = int(time.time())
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO git_connections
               (user_id,provider,host,status,created_at,updated_at)
               VALUES (?,?,?,'pending',?,?)
               ON CONFLICT(user_id,provider,host) DO UPDATE SET
                 status=CASE WHEN git_connections.status='active'
                   THEN git_connections.status ELSE 'pending' END,
                 updated_at=excluded.updated_at""",
            (user_id, provider, host, now, now),
        )
        row = connection.execute(
            """SELECT id,user_id,provider,host,external_user_id,external_login,
                      avatar_url,scopes_json,status,expires_at,created_at,updated_at,last_used_at
               FROM git_connections WHERE user_id=? AND provider=? AND host=?""",
            (user_id, provider, host),
        ).fetchone()
    assert row is not None
    return row


def activate_connection(
    user_id: int,
    connection_id: int,
    *,
    external_user_id: str,
    external_login: str,
    avatar_url: str | None,
    vault_ref: str,
    scopes: Iterable[str],
    expires_at: int | None,
) -> sqlite3.Row | None:
    vault_ref = validate_vault_ref(vault_ref, user_id, connection_id)
    external_user_id = _required_metadata(external_user_id, "external_user_id")
    external_login = _required_metadata(external_login, "external_login")
    normalized_scopes = sorted({value.strip() for value in scopes if value.strip()})
    now = int(time.time())
    with db.conn() as connection:
        cursor = connection.execute(
            """UPDATE git_connections SET
                 external_user_id=?,external_login=?,avatar_url=?,vault_ref=?,
                 scopes_json=?,status='active',expires_at=?,updated_at=?
               WHERE id=? AND user_id=?""",
            (
                external_user_id,
                external_login,
                avatar_url.strip()[:1000] if avatar_url else None,
                vault_ref,
                json.dumps(normalized_scopes, separators=(",", ":")),
                expires_at,
                now,
                connection_id,
                user_id,
            ),
        )
        if not cursor.rowcount:
            return None
        return _get_public_connection(connection, user_id, connection_id)


def _get_public_connection(
    connection: sqlite3.Connection, user_id: int, connection_id: int
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT id,user_id,provider,host,external_user_id,external_login,
                  avatar_url,scopes_json,status,expires_at,created_at,updated_at,last_used_at
           FROM git_connections WHERE id=? AND user_id=?""",
        (connection_id, user_id),
    ).fetchone()


def get_connection(user_id: int, connection_id: int) -> sqlite3.Row | None:
    with db.conn() as connection:
        return _get_public_connection(connection, user_id, connection_id)


def list_connections(user_id: int) -> list[sqlite3.Row]:
    now = int(time.time())
    with db.conn() as connection:
        connection.execute(
            """UPDATE git_connections SET status='expired',updated_at=?
               WHERE user_id=? AND status='active' AND expires_at IS NOT NULL AND expires_at<=?""",
            (now, user_id, now),
        )
        return list(
            connection.execute(
                """SELECT id,user_id,provider,host,external_user_id,external_login,
                          avatar_url,scopes_json,status,expires_at,created_at,updated_at,last_used_at
                   FROM git_connections WHERE user_id=? ORDER BY updated_at DESC,id DESC""",
                (user_id,),
            )
        )


def revoke_connection(user_id: int, connection_id: int) -> bool:
    now = int(time.time())
    with db.conn() as connection:
        cursor = connection.execute(
            """UPDATE git_connections
               SET status='revoked',vault_ref=NULL,updated_at=?
               WHERE id=? AND user_id=?""",
            (now, connection_id, user_id),
        )
        if cursor.rowcount:
            connection.execute(
                "UPDATE git_repository_grants SET enabled=0,updated_at=? WHERE connection_id=?",
                (now, connection_id),
            )
        return bool(cursor.rowcount)


def grant_repository(
    user_id: int,
    connection_id: int,
    *,
    external_repository_id: str,
    owner_name: str,
    repository_name: str,
    clone_url: str,
    default_branch: str | None,
    permission: str,
) -> sqlite3.Row:
    permission = permission.strip().lower()
    if permission not in PERMISSIONS:
        raise GitConnectionError("Некорректный repository permission")
    external_repository_id = _required_metadata(external_repository_id, "external_repository_id")
    owner_name = _required_metadata(owner_name, "owner_name")
    repository_name = _required_metadata(repository_name, "repository_name")
    now = int(time.time())
    with db.conn() as connection:
        current = connection.execute(
            "SELECT host,status FROM git_connections WHERE id=? AND user_id=?",
            (connection_id, user_id),
        ).fetchone()
        if current is None:
            raise GitConnectionError("Git connection недоступен")
        if current["status"] != "active":
            raise GitConnectionError("Git connection не активен")
        host = str(current["host"]).rsplit(":", 1)[0]
        clone_url = validate_repository_url(clone_url.strip(), (host,))
        connection.execute(
            """INSERT INTO git_repository_grants
               (connection_id,external_repository_id,owner_name,repository_name,clone_url,
                default_branch,permission,enabled,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,1,?,?)
               ON CONFLICT(connection_id,external_repository_id) DO UPDATE SET
                 owner_name=excluded.owner_name,
                 repository_name=excluded.repository_name,
                 clone_url=excluded.clone_url,
                 default_branch=excluded.default_branch,
                 permission=excluded.permission,
                 enabled=1,
                 updated_at=excluded.updated_at""",
            (
                connection_id,
                external_repository_id,
                owner_name,
                repository_name,
                clone_url,
                default_branch.strip()[:255] if default_branch else None,
                permission,
                now,
                now,
            ),
        )
        row = connection.execute(
            """SELECT g.* FROM git_repository_grants g
               JOIN git_connections c ON c.id=g.connection_id
               WHERE g.connection_id=? AND g.external_repository_id=? AND c.user_id=?""",
            (connection_id, external_repository_id, user_id),
        ).fetchone()
    assert row is not None
    return row


def sync_repository_catalog(
    user_id: int, connection_id: int, repositories: Iterable[RepositoryMetadata]
) -> None:
    """Обновляет metadata, не включая новые repositories без выбора пользователя."""
    now = int(time.time())
    with db.conn() as connection:
        current = connection.execute(
            "SELECT host,status FROM git_connections WHERE id=? AND user_id=?",
            (connection_id, user_id),
        ).fetchone()
        if current is None or current["status"] != "active":
            raise GitConnectionError("Git connection не активен")
        host = str(current["host"]).rsplit(":", 1)[0]
        seen: set[str] = set()
        for repository in repositories:
            external_id = _required_metadata(
                repository.external_repository_id, "external_repository_id"
            )
            permission = repository.permission.strip().lower()
            if permission not in PERMISSIONS:
                raise GitConnectionError("Некорректный repository permission")
            clone_url = validate_repository_url(repository.clone_url.strip(), (host,))
            seen.add(external_id)
            connection.execute(
                """INSERT INTO git_repository_grants
                   (connection_id,external_repository_id,owner_name,repository_name,clone_url,
                    default_branch,permission,enabled,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,0,?,?)
                   ON CONFLICT(connection_id,external_repository_id) DO UPDATE SET
                     owner_name=excluded.owner_name,
                     repository_name=excluded.repository_name,
                     clone_url=excluded.clone_url,
                     default_branch=excluded.default_branch,
                     permission=excluded.permission,
                     updated_at=excluded.updated_at""",
                (
                    connection_id,
                    external_id,
                    _required_metadata(repository.owner_name, "owner_name"),
                    _required_metadata(repository.repository_name, "repository_name"),
                    clone_url,
                    repository.default_branch.strip()[:255] if repository.default_branch else None,
                    permission,
                    now,
                    now,
                ),
            )
        if seen:
            placeholders = ",".join("?" for _ in seen)
            connection.execute(
                f"""UPDATE git_repository_grants SET enabled=0,updated_at=?
                    WHERE connection_id=? AND external_repository_id NOT IN ({placeholders})""",
                (now, connection_id, *sorted(seen)),
            )
        else:
            connection.execute(
                "UPDATE git_repository_grants SET enabled=0,updated_at=? WHERE connection_id=?",
                (now, connection_id),
            )


def set_repository_enabled(
    user_id: int, connection_id: int, external_repository_id: str, enabled: bool
) -> sqlite3.Row | None:
    external_id = _required_metadata(external_repository_id, "external_repository_id")
    now = int(time.time())
    with db.conn() as connection:
        cursor = connection.execute(
            """UPDATE git_repository_grants SET enabled=?,updated_at=?
               WHERE connection_id=? AND external_repository_id=?
                 AND EXISTS (
                   SELECT 1 FROM git_connections c
                   WHERE c.id=git_repository_grants.connection_id
                     AND c.user_id=? AND c.status='active'
                 )""",
            (int(enabled), now, connection_id, external_id, user_id),
        )
        if not cursor.rowcount:
            return None
        return connection.execute(
            """SELECT g.* FROM git_repository_grants g
               JOIN git_connections c ON c.id=g.connection_id
               WHERE g.connection_id=? AND g.external_repository_id=? AND c.user_id=?""",
            (connection_id, external_id, user_id),
        ).fetchone()


def list_repository_grants(user_id: int, connection_id: int | None = None) -> list[sqlite3.Row]:
    where = "c.user_id=?"
    arguments: list[object] = [user_id]
    if connection_id is not None:
        where += " AND c.id=?"
        arguments.append(connection_id)
    with db.conn() as connection:
        return list(
            connection.execute(
                f"""SELECT g.* FROM git_repository_grants g
                    JOIN git_connections c ON c.id=g.connection_id
                    WHERE {where} ORDER BY g.updated_at DESC,g.id DESC""",
                arguments,
            )
        )
