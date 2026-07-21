from pathlib import Path
from unittest.mock import MagicMock

import pytest

import manage_actions
from manage_actions import do_login, start_bot
from manage_config import PROVIDERS
from manage_process import BotProcessState


def test_qwen_token_plan_uses_current_preview_model_by_default() -> None:
    assert PROVIDERS["4"]["default_model"] == "qwen3.8-max-preview"


def test_codex_login_uses_isolated_home_and_login_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(
        manage_actions,
        "run_visible",
        lambda argv, env: calls.append((argv, env)) or 0,
    )
    monkeypatch.setattr(manage_actions, "is_logged_in", lambda *_args: (True, "auth.json"))

    do_login(PROVIDERS["2"], tmp_path)

    assert calls == [(["codex", "login"], {"CODEX_HOME": str(tmp_path)})]


def test_gemini_login_isolates_both_home_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(
        manage_actions,
        "run_visible",
        lambda argv, env: calls.append((argv, env)) or 0,
    )
    monkeypatch.setattr(manage_actions, "is_logged_in", lambda *_args: (False, ""))

    do_login(PROVIDERS["3"], tmp_path)

    argv, env = calls[0]
    assert argv == ["gemini"]
    assert env["HOME"] == str(tmp_path)
    assert env["USERPROFILE"] == str(tmp_path)


def test_qwen_login_isolates_qwen_home_and_opens_tui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(
        manage_actions,
        "run_visible",
        lambda argv, env: calls.append((argv, env)) or 0,
    )
    monkeypatch.setattr(manage_actions, "is_logged_in", lambda *_args: (False, ""))

    do_login(PROVIDERS["4"], tmp_path)

    argv, env = calls[0]
    assert argv == ["qwen"]
    assert env["QWEN_HOME"] == str(tmp_path / ".qwen")
    assert env["QWEN_RUNTIME_DIR"] == str(tmp_path / ".qwen")


def test_start_bot_refuses_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    monkeypatch.setattr(manage_actions, "bot_process_state", lambda _path: BotProcessState(False))
    monkeypatch.setattr(
        manage_actions, "env_state", lambda _path: {"token_set": False, "admin_set": False}
    )
    monkeypatch.setattr(manage_actions.subprocess, "call", process)

    start_bot()

    process.assert_not_called()


def test_start_bot_runs_with_configured_logged_account(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock(return_value=0)
    monkeypatch.setattr(
        manage_actions, "env_state", lambda _path: {"token_set": True, "admin_set": True}
    )
    monkeypatch.setattr(
        manage_actions,
        "list_accounts",
        lambda _path: [
            {
                "provider": "claude_code",
                "cli_home_path": "/tmp/home",
                "label": "main",
                "enabled": 1,
            }
        ],
    )
    monkeypatch.setattr(manage_actions, "bot_process_state", lambda _path: BotProcessState(False))
    monkeypatch.setattr(manage_actions, "is_logged_in", lambda *_args: (True, "marker"))
    monkeypatch.setattr(manage_actions.subprocess, "call", process)

    start_bot()

    process.assert_called_once()
    assert process.call_args.args[0][-1].endswith("bot.py")


def test_start_bot_accepts_inaccessible_os_runner_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = MagicMock(return_value=0)
    monkeypatch.setattr(
        manage_actions, "env_state", lambda _path: {"token_set": True, "admin_set": True}
    )
    monkeypatch.setattr(
        manage_actions,
        "list_accounts",
        lambda _path: [
            {
                "provider": "claude_code",
                "cli_home_path": "/protected",
                "label": "main",
                "enabled": 1,
            }
        ],
    )
    monkeypatch.setattr(manage_actions, "bot_process_state", lambda _path: BotProcessState(False))
    monkeypatch.setattr(
        manage_actions,
        "is_logged_in",
        lambda *_args: (False, manage_actions.LOGIN_STATE_INACCESSIBLE),
    )
    monkeypatch.setattr(manage_actions.subprocess, "call", process)

    start_bot()

    process.assert_called_once()


def test_start_bot_does_not_start_second_live_process(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    process = MagicMock()
    monkeypatch.setattr(
        manage_actions,
        "bot_process_state",
        lambda _path: BotProcessState(True, pid=321, uptime_minutes=5),
    )
    monkeypatch.setattr(manage_actions.subprocess, "call", process)

    start_bot()

    process.assert_not_called()
    assert "уже запущен" in capsys.readouterr().out
