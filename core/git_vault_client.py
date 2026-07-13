"""Application-side client for the root-owned Git vault admin boundary."""

from __future__ import annotations

import asyncio
import json
import os

from . import config


class GitVaultClientError(RuntimeError):
    pass


def admin_command(user_id: int, connection_id: int, operation: str) -> list[str]:
    if operation not in ("put", "revoke", "refresh") or connection_id < 1:
        raise GitVaultClientError("Git vault request невалиден")
    unix_user = config.OS_GIT_RUNNER_MAP.get(user_id)
    if not config.OS_RUNNERS_ENABLED or not unix_user:
        raise GitVaultClientError("Git runner не настроен")
    executable = config.GIT_VAULT_ADMIN_EXECUTABLE
    if not executable.startswith("/"):
        raise GitVaultClientError("Git vault admin path невалиден")
    return [
        "/usr/bin/sudo",
        "-n",
        executable,
        "--unix-user",
        unix_user,
        "--connection-id",
        str(connection_id),
        operation,
    ]


async def update_credential(
    user_id: int,
    connection_id: int,
    *,
    username: str | None = None,
    password: str | None = None,
    refresh_token: str | None = None,
) -> None:
    operation = "put" if username is not None and password is not None else "revoke"
    if operation == "put":
        payload = json.dumps(
            {
                "username": username,
                "password": password,
                **({"refresh_token": refresh_token} if refresh_token else {}),
            },
            separators=(",", ":"),
        ).encode()
    else:
        payload = b"{}"
    command = admin_command(user_id, connection_id, operation)
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )
        await asyncio.wait_for(process.communicate(payload), timeout=30)
    except (OSError, asyncio.TimeoutError) as error:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        raise GitVaultClientError("Git vault update не выполнен") from error
    if process.returncode:
        raise GitVaultClientError("Git vault update отклонён")


async def refresh_credential(user_id: int, connection_id: int) -> int:
    command = admin_command(user_id, connection_id, "refresh")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            },
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(b""), timeout=30)
    except (OSError, asyncio.TimeoutError) as error:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        raise GitVaultClientError("Git credential refresh не выполнен") from error
    if process.returncode or len(stdout) > 1024:
        raise GitVaultClientError("Git credential refresh отклонён")
    try:
        payload = json.loads(stdout)
        expires_at = int(payload["expires_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GitVaultClientError("Git credential refresh response невалиден") from error
    return expires_at
