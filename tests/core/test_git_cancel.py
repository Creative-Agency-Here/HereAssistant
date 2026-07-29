"""Отмена git-операции не оставляет живой процесс в каталоге проекта."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from core import git_projects


class HangingProcess:
    """Процесс, который сам не завершается — как долгий git clone."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.reaped = False

    async def communicate(self, _payload: bytes | None = None) -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        raise AssertionError("недостижимо")

    async def wait(self) -> int:
        if not self.killed:
            await asyncio.Event().wait()
        self.reaped = True
        return self.returncode or -9

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_run_git_kills_process_on_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Иначе git продолжает писать в каталог, который вызывающий уже удаляет."""
    proc: Any = HangingProcess()

    async def fake_exec(*_args: object, **_kwargs: object) -> Any:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(git_projects.run_git("clone", user_id=1, cwd=tmp_path))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed, "отмена обязана убить git-процесс"
    assert proc.reaped, "убитый процесс обязан быть собран"


@pytest.mark.asyncio
async def test_run_git_kills_process_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc: Any = HangingProcess()

    async def fake_exec(*_args: object, **_kwargs: object) -> Any:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(git_projects.GitProjectError, match="timeout"):
        await git_projects.run_git("clone", user_id=1, cwd=tmp_path, timeout=1)

    assert proc.killed
    assert proc.reaped
