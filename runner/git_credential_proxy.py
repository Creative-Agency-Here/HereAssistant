#!/usr/bin/python3
"""Минимальный Git credential-helper proxy к локальному vault broker socket."""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_MESSAGE_BYTES = 65_536
HOST_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?$")
PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")


class CredentialProxyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialRequest:
    operation: str
    access: str
    protocol: str
    host: str
    path: str


def parse_request(operation: str, payload: str, access: str = "read") -> CredentialRequest | None:
    """Принимает только HTTPS get-запрос для конкретного repository path."""
    if operation in ("store", "erase"):
        return None
    if (
        operation != "get"
        or access not in ("read", "write")
        or len(payload.encode("utf-8")) > MAX_MESSAGE_BYTES
    ):
        raise CredentialProxyError("credential operation запрещена")
    values: dict[str, str] = {}
    for line in payload.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"protocol", "host", "path"}:
            values[key] = value.strip()
    protocol = values.get("protocol", "").lower()
    host = values.get("host", "").lower()
    repository_path = values.get("path", "").lstrip("/")
    parts = Path(repository_path).parts
    if (
        protocol != "https"
        or not HOST_PATTERN.fullmatch(host)
        or not repository_path
        or not PATH_PATTERN.fullmatch(repository_path)
        or ".." in parts
    ):
        raise CredentialProxyError("credential target запрещён")
    return CredentialRequest(
        operation="get",
        access=access,
        protocol=protocol,
        host=host,
        path=repository_path,
    )


def request_credential(socket_path: Path, request: CredentialRequest) -> tuple[str, str]:
    if not socket_path.is_absolute():
        raise CredentialProxyError("vault socket должен быть абсолютным")
    try:
        socket_stat = socket_path.stat()
        parent_stat = socket_path.parent.stat()
    except OSError as error:
        raise CredentialProxyError("vault broker недоступен") from error
    if (
        not stat.S_ISSOCK(socket_stat.st_mode)
        or socket_stat.st_mode & 0o002
        or parent_stat.st_mode & 0o002
    ):
        raise CredentialProxyError("vault socket permissions запрещены")
    message = json.dumps(asdict(request), separators=(",", ":")).encode() + b"\n"
    chunks: list[bytes] = []
    total = 0
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(socket_path))
            client.sendall(message)
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_MESSAGE_BYTES:
                    raise CredentialProxyError("vault response слишком большой")
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
    except OSError as error:
        raise CredentialProxyError("vault broker недоступен") from error
    try:
        payload = json.loads(b"".join(chunks).split(b"\n", 1)[0])
        username = str(payload["username"])
        password = str(payload["password"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CredentialProxyError("vault response невалиден") from error
    if (
        not username
        or not password
        or len(username) > 1024
        or len(password) > 16_384
        or "\n" in username
        or "\n" in password
    ):
        raise CredentialProxyError("vault credential невалиден")
    return username, password


def main() -> int:
    operation = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        access = os.environ.get("HEREASSISTANT_GIT_ACCESS", "")
        request = parse_request(operation, sys.stdin.read(MAX_MESSAGE_BYTES + 1), access)
        if request is None:
            return 0
        socket_path = Path(os.environ.get("HEREASSISTANT_GIT_VAULT_SOCKET", ""))
        username, password = request_credential(socket_path, request)
    except CredentialProxyError:
        # Git получает только общий отказ; target и credential не логируются.
        return 1
    sys.stdout.write(f"username={username}\npassword={password}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
