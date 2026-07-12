import asyncio
from pathlib import Path
from typing import Any

import pytest

from providers import process


class HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.wait_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        if not self.killed:
            await asyncio.Event().wait()
        return self.returncode or -9

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_resolve_cli_argv_is_noop_outside_windows() -> None:
    argv = ["claude", "--print"]

    assert process.resolve_cli_argv(argv, windows=False) == argv


@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_resolve_cli_argv_wraps_windows_batch_files(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    resolved = str(Path("C:/tools/claude").with_suffix(suffix))
    monkeypatch.setattr(process.shutil, "which", lambda _name: resolved)

    assert process.resolve_cli_argv(["claude", "--print"], windows=True) == [
        "cmd",
        "/c",
        resolved,
        "--print",
    ]


def test_resolve_cli_argv_supports_gemini_powershell_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = "C:/tools/gemini.ps1"
    monkeypatch.setattr(process.shutil, "which", lambda _name: resolved)

    assert process.resolve_cli_argv(["gemini", "-p", ""], allow_powershell=True, windows=True) == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        resolved,
        "-p",
        "",
    ]


def test_resolve_cli_argv_uses_direct_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = "C:/tools/claude.exe"
    monkeypatch.setattr(process.shutil, "which", lambda _name: resolved)

    assert process.resolve_cli_argv(["claude", "--print"], windows=True) == [
        resolved,
        "--print",
    ]


def test_resolve_cli_argv_rejects_missing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="Не найдена команда 'claude'"):
        process.resolve_cli_argv(["claude"], windows=True)


@pytest.mark.asyncio
async def test_finish_process_kills_and_reaps_after_timeout() -> None:
    child: Any = HangingProcess()

    await process.finish_process(child, timeout=0.001)

    assert child.killed
    assert child.returncode == -9
    assert child.wait_calls == 2
