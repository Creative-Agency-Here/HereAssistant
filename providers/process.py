"""Общие безопасные операции lifecycle для CLI subprocess-провайдеров."""

from __future__ import annotations

import asyncio
import os
import shutil


def resolve_cli_argv(
    argv: list[str], *, allow_powershell: bool = False, windows: bool | None = None
) -> list[str]:
    """Разрешает npm CLI wrappers на Windows, не включая shell без необходимости."""
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return argv
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise RuntimeError(f"Не найдена команда '{argv[0]}'")
    if resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", resolved, *argv[1:]]
    if allow_powershell and resolved.lower().endswith(".ps1"):
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            resolved,
            *argv[1:],
        ]
    argv[0] = resolved
    return argv


async def write_stdin(proc: asyncio.subprocess.Process, stdin_data: str | None) -> None:
    if stdin_data is None or proc.stdin is None:
        return
    try:
        proc.stdin.write(stdin_data.encode("utf-8"))
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        try:
            proc.stdin.close()
        except Exception:
            # Закрытие stdin после завершения CLI — best effort.
            pass


async def finish_process(proc: asyncio.subprocess.Process, timeout: float = 5) -> None:
    """Дожидается процесса; после timeout гарантированно посылает kill и reap."""
    if proc.returncode is not None:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            # Процесс уже мог быть собран ОС после kill.
            pass
