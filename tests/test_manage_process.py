from pathlib import Path

import pytest

import manage_process
from manage_process import login_markers, login_state, npm_install_argv, run_visible


@pytest.mark.parametrize(
    ("provider", "relative"),
    [
        ("claude_code", ".credentials.json"),
        ("codex", "auth.json"),
        ("gemini", ".gemini/oauth_creds.json"),
        ("qwen_code", ".qwen/settings.json"),
    ],
)
def test_login_state_detects_provider_auth_marker(
    tmp_path: Path, provider: str, relative: str
) -> None:
    marker = tmp_path / relative
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")

    logged_in, hint = login_state(provider, tmp_path)

    assert logged_in
    assert marker.name in hint


def test_login_state_is_false_for_missing_home_unknown_provider_and_no_marker(
    tmp_path: Path,
) -> None:
    assert login_state("claude_code", tmp_path / "missing") == (False, "")
    assert login_state("unknown", tmp_path) == (False, "")
    assert login_state("codex", tmp_path) == (False, "")
    assert login_markers("unknown", tmp_path) == ()


def test_login_state_handles_inaccessible_os_runner_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_exists = Path.exists

    def exists(path: Path) -> bool:
        if path.name == ".credentials.json":
            raise PermissionError(path)
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", exists)

    assert login_state("claude_code", tmp_path) == (
        False,
        manage_process.LOGIN_STATE_INACCESSIBLE,
    )


def test_bot_process_state_distinguishes_live_and_stale_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_file = tmp_path / "bot.lock"
    lock_file.write_text("123|1000", encoding="utf-8")
    monkeypatch.setattr(manage_process.os, "kill", lambda _pid, _signal: None)
    monkeypatch.setattr(manage_process.time, "time", lambda: 2200)

    live = manage_process.bot_process_state(lock_file)

    assert live.running and live.pid == 123 and live.uptime_minutes == 20

    def missing_process(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(manage_process.os, "kill", missing_process)
    assert not manage_process.bot_process_state(lock_file).running
    assert not manage_process.bot_process_state(tmp_path / "missing.lock").running


def test_npm_argv_wraps_windows_command_shims_only() -> None:
    assert npm_install_argv("pkg", npm_path="C:/npm.cmd", windows=True) == [
        "cmd",
        "/c",
        "npm",
        "install",
        "-g",
        "pkg",
    ]
    assert npm_install_argv("pkg", npm_path="/usr/bin/npm", windows=False) == [
        "npm",
        "install",
        "-g",
        "pkg",
    ]


def test_run_visible_merges_environment_without_mutating_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_call(argv: list[str], *, env: dict[str, str]) -> int:
        captured.update(argv=argv, env=env)
        return 7

    monkeypatch.setenv("BASE_VALUE", "base")
    monkeypatch.setattr(manage_process.subprocess, "call", fake_call)

    assert run_visible(["tool", "arg"], {"EXTRA_VALUE": "extra"}) == 7
    assert captured["argv"] == ["tool", "arg"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["BASE_VALUE"] == "base"
    assert env["EXTRA_VALUE"] == "extra"
