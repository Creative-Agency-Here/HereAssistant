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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

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


@dataclass(frozen=True)
class StoredCredential:
    username: str
    password: str
    refresh_token: str | None = None


def parse_secret_request(operation: str, payload: bytes) -> StoredCredential | None:
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
        or not {"username", "password"} <= set(raw)
        or set(raw) - {"username", "password", "refresh_token"}
    ):
        raise GitVaultAdminError("invalid put request")
    username = raw.get("username")
    password = raw.get("password")
    refresh_token = raw.get("refresh_token")
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not username
        or not password
        or len(username) > 1_024
        or len(password) > 32_768
        or any(character in username for character in "\r\n\0")
        or any(character in password for character in "\r\n\0")
        or (
            refresh_token is not None
            and (
                not isinstance(refresh_token, str)
                or not refresh_token
                or len(refresh_token) > 32_768
                or any(character in refresh_token for character in "\r\n\0")
            )
        )
    ):
        raise GitVaultAdminError("invalid credential")
    return StoredCredential(username=username, password=password, refresh_token=refresh_token)


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


def connection_refresh_target(database: Path, user_id: int, connection_id: int) -> tuple[str, str]:
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT provider,host,status FROM git_connections WHERE id=? AND user_id=?",
                (connection_id, user_id),
            ).fetchone()
    except sqlite3.Error as error:
        raise GitVaultAdminError("grant database unavailable") from error
    if row is None or row[0] != "gitea" or row[2] not in ("active", "expired"):
        raise GitVaultAdminError("refresh target unavailable")
    return str(row[0]), str(row[1]).lower()


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
            or not {"username", "password"} <= set(value)
            or set(value) - {"username", "password", "refresh_token"}
        ):
            raise GitVaultAdminError("credential bundle invalid")
        credential = parse_secret_request("put", json.dumps(value).encode())
        assert credential is not None
        result[vault_ref] = {
            "username": credential.username,
            "password": credential.password,
        }
        if credential.refresh_token:
            result[vault_ref]["refresh_token"] = credential.refresh_token
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
    credential: StoredCredential | VaultCredential | None,
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
        refresh_token = getattr(credential, "refresh_token", None)
        if refresh_token:
            bundle[vault_ref]["refresh_token"] = refresh_token
    plaintext = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    encrypted = command_runner(
        ["systemd-creds", "encrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
        plaintext,
    )
    if not encrypted or len(encrypted) > MAX_BUNDLE_BYTES * 2:
        raise GitVaultAdminError("encrypted bundle invalid")
    _atomic_write(encrypted_path, encrypted)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _exchange_refresh_token(host: str, client_id: str, refresh_token: str) -> tuple[str, str, int]:
    request = Request(
        f"https://{host}/login/oauth/access_token",
        data=json.dumps(
            {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            separators=(",", ":"),
        ).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=15) as response:
            payload = response.read(MAX_REQUEST_BYTES + 1)
            if response.status < 200 or response.status >= 300:
                raise GitVaultAdminError("refresh response rejected")
    except (HTTPError, URLError, OSError) as error:
        raise GitVaultAdminError("refresh request failed") from error
    if len(payload) > MAX_REQUEST_BYTES:
        raise GitVaultAdminError("refresh response too large")
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GitVaultAdminError("refresh response invalid") from error
    if not isinstance(value, dict):
        raise GitVaultAdminError("refresh response invalid")
    access_token = value.get("access_token")
    rotated_refresh = value.get("refresh_token") or refresh_token
    expires_in = value.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not access_token
        or len(access_token) > 32_768
        or not isinstance(rotated_refresh, str)
        or not rotated_refresh
        or len(rotated_refresh) > 32_768
        or not isinstance(expires_in, (int, float))
        or not 0 < int(expires_in) <= 31_536_000
        or any(character in access_token for character in "\r\n\0")
        or any(character in rotated_refresh for character in "\r\n\0")
    ):
        raise GitVaultAdminError("refresh response invalid")
    return access_token, rotated_refresh, int(time.time()) + int(expires_in)


def refresh_bundle(
    encrypted_path: Path,
    vault_ref: str,
    host: str,
    client_id: str,
    *,
    command_runner: CredentialCommand = _run_systemd_creds,
    token_exchange: Callable[[str, str, str], tuple[str, str, int]] = _exchange_refresh_token,
) -> int:
    if not encrypted_path.exists():
        raise GitVaultAdminError("credential bundle unavailable")
    decrypted = command_runner(
        ["systemd-creds", "decrypt", f"--name={CREDENTIAL_NAME}", str(encrypted_path), "-"],
        None,
    )
    bundle = _parse_bundle(decrypted)
    value = bundle.get(vault_ref)
    if value is None or not value.get("refresh_token"):
        raise GitVaultAdminError("refresh credential unavailable")
    access_token, refresh_token, expires_at = token_exchange(
        host, client_id, value["refresh_token"]
    )
    value["password"] = access_token
    value["refresh_token"] = refresh_token
    plaintext = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    encrypted = command_runner(
        ["systemd-creds", "encrypt", f"--name={CREDENTIAL_NAME}", "-", "-"], plaintext
    )
    if not encrypted or len(encrypted) > MAX_BUNDLE_BYTES * 2:
        raise GitVaultAdminError("encrypted bundle invalid")
    _atomic_write(encrypted_path, encrypted)
    return expires_at


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
    parser.add_argument("operation", choices=("put", "revoke", "refresh"))
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
        vault_ref = connection_vault_ref(
            config.git_database, config.user_id, arguments.connection_id
        )
        encrypted_path = ENCRYPTED_DIRECTORY / f"{arguments.unix_user}.json.cred"
        _validate_storage(ENCRYPTED_DIRECTORY, encrypted_path)
        if arguments.operation == "refresh":
            if payload.strip():
                raise GitVaultAdminError("refresh request must be empty")
            _provider, host = connection_refresh_target(
                config.git_database, config.user_id, arguments.connection_id
            )
            client_id = config.gitea_oauth_apps.get(host)
            if not client_id:
                raise GitVaultAdminError("refresh app unavailable")
            expires_at = refresh_bundle(encrypted_path, vault_ref, host, client_id)
            print(json.dumps({"expires_at": expires_at}, separators=(",", ":")))
        else:
            credential = parse_secret_request(arguments.operation, payload)
            rotate_bundle(encrypted_path, vault_ref, credential)
        _reload_active_service(arguments.unix_user)
    except (GitVaultAdminError, OSError):
        print("git vault admin denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
