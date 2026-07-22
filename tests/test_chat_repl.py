from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

import chat
from chat_sessions import AccountRecord, Session


class FakeTitle:
    def idle(self, cwd: str, open_tasks: int = 0) -> None:
        pass

    def start(self, prompt: str, task_count: int) -> None:
        pass

    async def finish(self, *, completed: bool, cwd: str, open_tasks: int = 0) -> None:
        pass


async def test_repl_runs_each_plain_prompt_once(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(("Сделай один раз", "/exit"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(values))
    monkeypatch.setattr(chat, "TerminalTitle", FakeTitle)
    monkeypatch.setattr(chat, "task_summary", lambda _cwd: {"open": 0})
    monkeypatch.setattr(
        chat,
        "workspace_overview",
        lambda _user_id, _cwd: {
            "tasks": {"open": 0},
            "git": {"repositories": 0},
            "repositoriesOnDisk": 0,
            "disk": {"freeLabel": "1 ГБ"},
        },
    )
    monkeypatch.setattr(chat, "_farewell", lambda: None)
    run_prompt = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(chat, "_run_prompt", run_prompt)
    account = cast(
        AccountRecord,
        {
            "label": "main",
            "provider": "codex",
            "default_model": "model",
            "enabled": True,
            "cli_home_path": "/tmp/home",
        },
    )
    monkeypatch.setattr(chat.config, "user_default_cwd", lambda _user_id: "/tmp")

    await chat._repl(Session(account, 1, "@alice"))  # noqa: SLF001

    run_prompt.assert_awaited_once()
    assert run_prompt.await_args is not None
    assert run_prompt.await_args.args[1] == "Сделай один раз"
