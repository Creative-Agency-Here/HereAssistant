#!/usr/bin/python3
"""Root-only atomic writer for encrypted per-user Git credential bundles.

OAuth/PAT values are accepted only as bounded JSON on stdin. They never appear
in argv, the SQLite database, process environment, stdout or error details.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from runner.entrypoint import load_config
from runner.git_vault_service import VaultCredential

ENCRYPTED_DIRECTORY = Path("/etc/hereassistant/git-credentials")
CREDENTIAL_NAME = "git-credentials.json"
MAX_REQUEST_BYTES = 65_536
MAX_BUNDLE_BYTES = 1_048_576
UNIX_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class GitVaultAdminError(RuntimeError):
    pass


CredentialCommand = Callable[[list[str], bytes | None], bytes]


def parse_secret_request(operation: str, payload: bytes) -> VaultCredential | None:
    if len(payload) > MAX_REQUEST_BYTES:
        raise GitVaultAdminError("request too large")
    if operation == "revoke" and not payload.strip():
        return None
    try:
        raw = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GitVaultAdminError("invalid request") from error
    if operation == "revoke":
        if raw != {}:
            raise GitVaultAdminError("revoke request must be empty")
        return None
    if (
        operation != "put"
        or not isinstance(raw, dict)
        or set(raw)
        != {
            "username",
            "password",
        }
    ):
        raise GitVaultAdminError("invalid put request")
    username = raw.get("username")
    password = raw.get("password")
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not username
        or not password
        or len(username) > 1_024
        or len(password) > 32_768
        or any(character in username for character in "\r\n\0")
        or any(character in password for character in "\r\n\0")
    ):
        raise GitVaultAdminError("invalid credential")
    return VaultCredential(username=username, password=password)


def connection_vault_ref(database: Path, user_id: int, connection_id: int) -> str:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        row = connection.execute(
            "SELECT user_id,vault_ref FROM git_connections WHERE id=? AND user_id=?",
            (connection_id, user_id),
        ).fetchone()
    except sqlite3.Error as error:
        raise GitVaultAdminError("grant database unavailable") from error
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        raise GitVaultAdminError("connection unavailable")
    expected = f"vault://git/{user_id}/{connection_id}/primary"
    current = str(row[1] or "")
    if current and current != expected:
        raise GitVaultAdminError("connection vault reference mismatch")
    return expected


def _parse_bundle(payload: bytes) -> dict[str, dict[str, str]]:
    if len(payload) > MAX_BUNDLE_BYTES:
        raise GitVaultAdminError("credential bundle too large")
    try:
        raw = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GitVaultAdminError("credential bundle invalid") from error
    if not isinstance(raw, dict):
        raise GitVaultAdminError("credential bundle invalid")
    result: dict[str, dict[str, str]] = {}
    for vault_ref, value in raw.items():
        if (
            not isinstance(vault_ref, str)
            or not vault_ref.startswith("vault://git/")
            or not isinstance(value, dict)
            or set(value) != {"username", "password"}
        ):
            raise GitVaultAdminError("credential bundle invalid")
        credential = parse_secret_request("put", json.dumps(value).encode())
        assert credential is not None
        result[vault_ref] = {
            "username": credential.username,
            "password": credential.password,
        }
    return result


def _run_systemd_creds(command: list[str], input_data: bytes | None) -> bytes:
    try:
        process = subprocess.run(
            command,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitVaultAdminError("systemd-creds unavailable") from error
    if process.returncode:
        raise GitVaultAdminError("systemd-creds failed")
    return process.stdout


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def rotate_bundle(
    encrypted_path: Path,
    vault_ref: str,
    credential: VaultCredential | None,
    *,
    command_runner: CredentialCommand = _run_systemd_creds,
) -> None:
    if encrypted_path.exists():
        decrypted = command_runner(
            ["systemd-creds", "decrypt", f"--name={CREDENTIAL_NAME}", str(encrypted_path), "-"],
            None,
        )
        bundle = _parse_bundle(decrypted)
    else:
        bundle = {}
    if credential is None:
        bundle.pop(vault_ref, None)
    else:
        bundle[vault_ref] = {
            "username": credential.username,
            "password": credential.password,
        }
    plaintext = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    encrypted = command_runner(
        ["systemd-creds", "encrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
        plaintext,
    )
    if not encrypted or len(encrypted) > MAX_BUNDLE_BYTES * 2:
        raise GitVaultAdminError("encrypted bundle invalid")
    _atomic_write(encrypted_path, encrypted)


def _validate_storage(directory: Path, encrypted_path: Path) -> None:
    try:
        directory_stat = directory.stat()
    except OSError as error:
        raise GitVaultAdminError("credential directory unavailable") from error
    if not directory.is_dir() or directory_stat.st_uid != 0 or directory_stat.st_mode & 0o077:
        raise GitVaultAdminError("credential directory permissions invalid")
    if encrypted_path.exists() or encrypted_path.is_symlink():
        current = encrypted_path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != 0
            or current.st_mode & 0o077
            or current.st_size > MAX_BUNDLE_BYTES * 2
        ):
            raise GitVaultAdminError("credential file permissions invalid")


def _reload_active_service(unix_user: str) -> None:
    try:
        process = subprocess.run(
            ["systemctl", "try-restart", f"hereassistant-git-vault@{unix_user}.service"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitVaultAdminError("vault reload failed") from error
    if process.returncode:
        raise GitVaultAdminError("vault reload failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unix-user", required=True)
    parser.add_argument("--connection-id", type=int, required=True)
    parser.add_argument("operation", choices=("put", "revoke"))
    arguments = parser.parse_args()
    try:
        if os.geteuid() != 0 or not UNIX_USER_PATTERN.fullmatch(arguments.unix_user):
            raise GitVaultAdminError("invalid execution identity")
        if arguments.connection_id < 1:
            raise GitVaultAdminError("invalid connection")
        config = load_config(arguments.unix_user)
        if not config.git_broker or config.git_database is None:
            raise GitVaultAdminError("invalid Git broker config")
        payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        credential = parse_secret_request(arguments.operation, payload)
        vault_ref = connection_vault_ref(
            config.git_database, config.user_id, arguments.connection_id
        )
        encrypted_path = ENCRYPTED_DIRECTORY / f"{arguments.unix_user}.json.cred"
        _validate_storage(ENCRYPTED_DIRECTORY, encrypted_path)
        rotate_bundle(encrypted_path, vault_ref, credential)
        _reload_active_service(arguments.unix_user)
    except (GitVaultAdminError, OSError):
        print("git vault admin denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
