"""Блокирующий PreToolUse-хук: единственная точка реальной остановки команды."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import risk_hook

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "claude_risk_hook.py"


def run_hook(payload: dict, *, env: dict[str, str] | None = None) -> dict | None:
    """Прогоняет хук как настоящий процесс и возвращает его решение."""
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    return json.loads(out) if out else None


def decision(payload: dict) -> str | None:
    answer = run_hook(payload)
    if answer is None:
        return None
    return answer["hookSpecificOutput"]["permissionDecision"]


def test_catastrophic_command_is_denied() -> None:
    answer = run_hook({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "cwd": "/tmp"})

    assert answer is not None
    output = answer["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "системный корень" in output["permissionDecisionReason"]


def test_home_and_keys_are_denied() -> None:
    for command in ("rm -rf ~", "rm -rf ~/.ssh", "echo x > /dev/disk0"):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}
        assert decision(payload) == "deny", command


def test_confirm_level_is_not_blocked() -> None:
    """CONFIRM остаётся предупреждением: ложная блокировка работы дороже."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf ../neighbour"},
        "cwd": "/work/project",
    }

    assert decision(payload) is None


def test_safe_commands_pass() -> None:
    for command in ("git status", "uv run pytest -q", "rm -rf ./build"):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/work/project"}
        assert decision(payload) is None, command


def test_non_shell_tools_are_ignored() -> None:
    payload = {"tool_name": "Read", "tool_input": {"command": "rm -rf /"}, "cwd": "/tmp"}

    assert decision(payload) is None


def test_broken_input_does_not_block_work() -> None:
    """Непонятный ввод — не повод останавливать агента."""
    result = subprocess.run(
        [sys.executable, str(HOOK)], input="не json", capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_hook_can_be_disabled_by_env() -> None:
    import os

    env = dict(os.environ, HEREASSISTANT_RISK_HOOK="0")
    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "cwd": "/tmp"}

    assert run_hook(payload, env=env) is None


def test_hook_command_quotes_paths_with_spaces() -> None:
    """Без кавычек Claude молча не запустит хук — защиты просто не будет."""
    command = risk_hook.hook_command("/usr/bin/python3")

    assert "claude_risk_hook.py" in command
    if " " in str(risk_hook.HOOK_SCRIPT):
        assert "'" in command or '"' in command


def test_configure_is_idempotent_and_keeps_foreign_hooks(tmp_path: Path) -> None:
    home = tmp_path / "auth_home"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "rtk hook"}]}]},
                "permissions": {"allow": ["Bash(ls:*)"]},
            }
        ),
        encoding="utf-8",
    )

    assert risk_hook.configure_claude_hook(home)
    assert risk_hook.configure_claude_hook(home)

    payload = json.loads(settings.read_text(encoding="utf-8"))
    entries = payload["hooks"]["PreToolUse"]
    ours = [
        entry
        for entry in entries
        if any("claude_risk_hook.py" in h.get("command", "") for h in entry["hooks"])
    ]
    assert len(ours) == 1, "повторный вызов не должен дублировать хук"
    assert any("rtk hook" in h.get("command", "") for e in entries for h in e["hooks"])
    assert payload["permissions"]["allow"] == ["Bash(ls:*)"], "чужие настройки сохраняются"
    assert risk_hook.is_configured(home)


def test_hook_timeout_is_short() -> None:
    """При таймауте Claude выполняет команду, поэтому запас должен быть мал."""
    assert risk_hook.HOOK_TIMEOUT_SEC <= 15


@pytest.mark.parametrize("broken", [{}, {"hooks": []}, {"hooks": {"PreToolUse": {}}}])
def test_configure_survives_broken_settings(tmp_path: Path, broken: dict) -> None:
    home = tmp_path / "auth_home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps(broken), encoding="utf-8")

    risk_hook.configure_claude_hook(home)


def test_qwen_tool_name_is_covered() -> None:
    """У Qwen Code shell-инструмент называется иначе, чем у Claude."""
    payload = {
        "tool_name": "run_shell_command",
        "tool_input": {"command": "rm -rf ~/.ssh"},
        "cwd": "/tmp",
    }

    assert decision(payload) == "deny"


def test_qwen_timeout_is_in_milliseconds() -> None:
    """У Claude таймаут в секундах, у Qwen — в миллисекундах.

    Одинаковое число означало бы у Qwen десять миллисекунд: хук не успел бы
    ответить, и защиты бы не было.
    """
    assert risk_hook.HOOK_TIMEOUT_MS == risk_hook.HOOK_TIMEOUT_SEC * 1000


def test_qwen_hook_uses_its_own_matcher(tmp_path: Path) -> None:
    home = tmp_path / "qwen_home"
    home.mkdir()

    assert risk_hook.configure_qwen_hook(home)

    payload = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    entry = payload["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "run_shell_command"
    assert entry["hooks"][0]["timeout"] == risk_hook.HOOK_TIMEOUT_MS
    assert risk_hook.is_configured(home)


def test_claude_hook_keeps_seconds(tmp_path: Path) -> None:
    home = tmp_path / "claude_home"
    home.mkdir()

    assert risk_hook.configure_claude_hook(home)

    entry = json.loads((home / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][
        0
    ]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0]["timeout"] == risk_hook.HOOK_TIMEOUT_SEC
