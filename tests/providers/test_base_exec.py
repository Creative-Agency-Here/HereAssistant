"""Инвариант: `_exec` не оставляет дочерний CLI живым при timeout и отмене."""

import asyncio
from typing import Any

import pytest

from core import config
from providers.base import CLIProvider


class FakeProcess:
    """Дочерний CLI, который сам никогда не завершается."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.reaped = False

    async def communicate(self, _stdin: bytes | None = None) -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        raise AssertionError("недостижимо: процесс не завершается сам")

    async def wait(self) -> int:
        if not self.killed:
            await asyncio.Event().wait()
        self.reaped = True
        return self.returncode or -9

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _provider_with(proc: FakeProcess) -> CLIProvider:
    """Провайдер без __init__: нужен только `_exec` поверх подменённого spawn."""
    provider: Any = object.__new__(CLIProvider)

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        return proc

    provider._spawn = fake_spawn
    return provider


@pytest.mark.asyncio
async def test_exec_kills_child_on_cancel() -> None:
    proc = FakeProcess()
    provider = _provider_with(proc)

    task = asyncio.create_task(provider._exec(["codex", "exec"], "/tmp"))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed, "отмена задачи обязана убить дочерний CLI"
    assert proc.reaped, "убитый процесс обязан быть собран, иначе останется зомби"


@pytest.mark.asyncio
async def test_exec_kills_child_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CLI_TIMEOUT", 0.01)
    proc = FakeProcess()
    provider = _provider_with(proc)

    with pytest.raises(RuntimeError, match="CLI timeout"):
        await provider._exec(["codex", "exec"], "/tmp")

    assert proc.killed
    assert proc.reaped
