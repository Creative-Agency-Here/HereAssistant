#!/usr/bin/python3
"""Linux vault broker для per-user Git credential-helper proxy."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from runner.entrypoint import load_config
from runner.git_credential_proxy import (
    HOST_PATTERN,
    MAX_MESSAGE_BYTES,
    PATH_PATTERN,
    CredentialRequest,
)

try:
    import pwd
except ImportError:  # Windows CI импортирует модуль, но service доступен только Linux.
    pwd = None  # type: ignore[assignment]

CREDENTIAL_BUNDLE = "git-credentials.json"


class GitVaultError(RuntimeError):
    pass


@dataclass(frozen=True)
class VaultCredential:
    username: str
    password: str


def load_credential_bundle(credentials_directory: Path) -> dict[str, VaultCredential]:
    bundle_path = credentials_directory / CREDENTIAL_BUNDLE
    try:
        bundle_stat = bundle_path.stat()
        if not bundle_path.is_file() or bundle_stat.st_mode & 0o022:
            raise GitVaultError("credential bundle permissions запрещены")
        if bundle_stat.st_size > 1_048_576:
            raise GitVaultError("credential bundle слишком большой")
        raw = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitVaultError("credential bundle недоступен") from error
    if not isinstance(raw, dict):
        raise GitVaultError("credential bundle невалиден")
    result: dict[str, VaultCredential] = {}
    for vault_ref, value in raw.items():
        if not isinstance(vault_ref, str) or not isinstance(value, dict):
            raise GitVaultError("credential bundle невалиден")
        username = value.get("username")
        password = value.get("password")
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or not username
            or not password
            or "\n" in username
            or "\n" in password
        ):
            raise GitVaultError("credential bundle невалиден")
        result[vault_ref] = VaultCredential(username, password)
    return result


def parse_socket_request(payload: bytes) -> CredentialRequest:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise GitVaultError("vault request слишком большой")
    try:
        raw = json.loads(payload.split(b"\n", 1)[0])
        request = CredentialRequest(
            operation=str(raw["operation"]),
            access=str(raw["access"]),
            protocol=str(raw["protocol"]),
            host=str(raw["host"]),
            path=str(raw["path"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GitVaultError("vault request невалиден") from error
    if (
        request.operation != "get"
        or request.access not in ("read", "write")
        or request.protocol != "https"
        or not HOST_PATTERN.fullmatch(request.host)
        or not PATH_PATTERN.fullmatch(request.path)
        or ".." in Path(request.path).parts
    ):
        raise GitVaultError("vault request запрещён")
    return request


def _matches_repository(request: CredentialRequest, clone_url: str) -> bool:
    parsed = urlsplit(clone_url)
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and parsed.netloc.lower() == request.host.lower()
        and parsed.path.lstrip("/") == request.path
    )


def resolve_credential(
    database: Path,
    user_id: int,
    request: CredentialRequest,
    credentials: dict[str, VaultCredential],
) -> VaultCredential:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT c.vault_ref,g.permission,g.clone_url
               FROM git_repository_grants g
               JOIN git_connections c ON c.id=g.connection_id
               WHERE c.user_id=? AND c.status='active' AND g.enabled=1""",
            (user_id,),
        ).fetchall()
    except sqlite3.Error as error:
        raise GitVaultError("grant database недоступна") from error
    finally:
        if connection is not None:
            connection.close()
    for row in rows:
        if not _matches_repository(request, str(row["clone_url"])):
            continue
        if request.access == "write" and row["permission"] not in ("write", "admin"):
            raise GitVaultError("write grant отсутствует")
        vault_ref = str(row["vault_ref"] or "")
        credential = credentials.get(vault_ref)
        if credential is None:
            raise GitVaultError("credential отсутствует")
        return credential
    raise GitVaultError("repository grant отсутствует")


def peer_uid(connection: socket.socket) -> int:
    peer_option = getattr(socket, "SO_PEERCRED", None)
    if not isinstance(peer_option, int):
        raise GitVaultError("SO_PEERCRED недоступен")
    payload = connection.getsockopt(socket.SOL_SOCKET, peer_option, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", payload)
    return uid


def _prepare_socket(socket_path: Path, git_uid: int, git_gid: int) -> socket.socket:
    parent = socket_path.parent
    try:
        parent_stat = parent.stat()
    except OSError as error:
        raise GitVaultError("vault socket directory отсутствует") from error
    if parent_stat.st_mode & 0o002:
        raise GitVaultError("vault socket directory world-writable")
    if socket_path.exists() or socket_path.is_symlink():
        current = socket_path.lstat()
        if current.st_uid != 0 or not stat.S_ISSOCK(current.st_mode):
            raise GitVaultError("существующий vault socket небезопасен")
        socket_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        os.chown(socket_path, 0, git_gid)
        os.chmod(socket_path, 0o660)
        server.listen(16)
    except OSError:
        server.close()
        raise
    return server


def receive_request(connection: socket.socket) -> bytes:
    connection.settimeout(5)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_MESSAGE_BYTES:
            raise GitVaultError("vault request слишком большой")
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks)


def serve(
    socket_path: Path,
    database: Path,
    user_id: int,
    git_uid: int,
    git_gid: int,
    credentials: dict[str, VaultCredential],
) -> None:
    with _prepare_socket(socket_path, git_uid, git_gid) as server:
        while True:
            connection, _ = server.accept()
            with connection:
                try:
                    if peer_uid(connection) != git_uid:
                        raise GitVaultError("peer UID запрещён")
                    payload = receive_request(connection)
                    request = parse_socket_request(payload)
                    credential = resolve_credential(database, user_id, request, credentials)
                    response = json.dumps(
                        {"username": credential.username, "password": credential.password},
                        separators=(",", ":"),
                    ).encode()
                    connection.sendall(response + b"\n")
                except (GitVaultError, OSError):
                    try:
                        connection.sendall(b'{"error":"denied"}\n')
                    except OSError:
                        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unix-user", required=True)
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if pwd is None:
            raise GitVaultError("Linux user database недоступна")
        identity = pwd.getpwnam(arguments.unix_user)
        runner_config = load_config(arguments.unix_user)
        if not runner_config.git_broker or runner_config.git_vault_socket is None:
            raise GitVaultError("Git broker config невалиден")
        credentials_directory = Path(os.environ.get("CREDENTIALS_DIRECTORY", ""))
        if not credentials_directory.is_absolute():
            raise GitVaultError("systemd credentials directory отсутствует")
        credentials = load_credential_bundle(credentials_directory)
        serve(
            runner_config.git_vault_socket,
            arguments.database.resolve(strict=True),
            runner_config.user_id,
            identity.pw_uid,
            identity.pw_gid,
            credentials,
        )
    except (KeyError, OSError, GitVaultError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
