import asyncio

import pytest

from core import config, git_vault_client


def configure_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "OS_RUNNERS_ENABLED", True)
    monkeypatch.setattr(config, "OS_GIT_RUNNER_MAP", {100: "ha-ilya-git"})
    monkeypatch.setattr(
        config, "GIT_VAULT_ADMIN_EXECUTABLE", "/usr/local/libexec/hereassistant-git-vault-admin"
    )


def test_admin_command_is_owner_mapped_and_has_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_runner(monkeypatch)

    command = git_vault_client.admin_command(100, 7, "put")

    assert command[-5:] == ["--unix-user", "ha-ilya-git", "--connection-id", "7", "put"]
    with pytest.raises(git_vault_client.GitVaultClientError, match="не настроен"):
        git_vault_client.admin_command(200, 7, "put")


async def test_credential_crosses_only_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_runner(monkeypatch)
    captured: dict[str, object] = {}

    class Process:
        returncode = 0

        async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
            captured["payload"] = payload
            return b"", b""

    async def create_process(*command: str, **kwargs: object) -> Process:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    await git_vault_client.update_credential(
        100,
        7,
        username="oauth-user",
        password="runtime-secret",
        refresh_token="refresh-secret",
    )

    command = " ".join(captured["command"])  # type: ignore[arg-type]
    assert "oauth-user" not in command
    assert "runtime-secret" not in command
    assert "refresh-secret" not in command
    assert b"runtime-secret" in captured["payload"]  # type: ignore[operator]
    assert b"refresh-secret" in captured["payload"]  # type: ignore[operator]
    assert "runtime-secret" not in str(captured["environment"])


async def test_refresh_returns_only_safe_expiry_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_runner(monkeypatch)
    captured: dict[str, object] = {}

    class Process:
        returncode = 0

        async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
            captured["payload"] = payload
            return b'{"expires_at":2000000000}', b""

    async def create_process(*command: str, **_kwargs: object) -> Process:
        captured["command"] = command
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    expires_at = await git_vault_client.refresh_credential(100, 7)

    assert expires_at == 2_000_000_000
    assert captured["payload"] == b""
    assert captured["command"][-1] == "refresh"  # type: ignore[index]
