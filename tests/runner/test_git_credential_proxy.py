import hashlib
import json
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from runner.git_credential_proxy import (
    CredentialProxyError,
    CredentialRequest,
    parse_request,
    request_credential,
)


def test_proxy_accepts_only_https_get_for_bounded_repository_path() -> None:
    request = parse_request(
        "get", "protocol=https\nhost=git.example.com\npath=alice/project.git\n\n"
    )

    assert request == CredentialRequest(
        operation="get",
        access="read",
        protocol="https",
        host="git.example.com",
        path="alice/project.git",
    )
    assert parse_request("store", "protocol=https\nhost=git.example.com\n") is None
    assert parse_request("erase", "protocol=https\nhost=git.example.com\n") is None


@pytest.mark.parametrize(
    "payload",
    [
        "protocol=http\nhost=git.example.com\npath=alice/project.git\n",
        "protocol=https\nhost=user@git.example.com\npath=alice/project.git\n",
        "protocol=https\nhost=git.example.com\npath=../private\n",
        "protocol=https\nhost=git.example.com\npath=\n",
    ],
)
def test_proxy_rejects_unsafe_targets(payload: str) -> None:
    with pytest.raises(CredentialProxyError):
        parse_request("get", payload)


def test_proxy_exchanges_only_metadata_over_unix_socket(tmp_path: Path) -> None:
    suffix = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    socket_dir = Path(tempfile.mkdtemp(prefix=f"ha-{suffix}-", dir="/tmp"))
    socket_path = socket_dir / "vault.sock"
    received: list[dict[str, str]] = []
    ready = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _ = server.accept()
            with connection:
                payload = connection.recv(4096).split(b"\n", 1)[0]
                received.append(json.loads(payload))
                response = {"username": "oauth-user", "password": "runtime-value"}
                connection.sendall(json.dumps(response).encode() + b"\n")

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(timeout=2)

    credential = request_credential(
        socket_path,
        CredentialRequest("get", "write", "https", "git.example.com", "alice/project.git"),
    )
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert received == [
        {
            "operation": "get",
            "access": "write",
            "protocol": "https",
            "host": "git.example.com",
            "path": "alice/project.git",
        }
    ]
    assert credential == ("oauth-user", "runtime-value")
    socket_path.unlink(missing_ok=True)
    socket_dir.rmdir()
