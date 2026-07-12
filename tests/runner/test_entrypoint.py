import sqlite3
from pathlib import Path

import pytest

from runner.entrypoint import (
    RunnerConfig,
    RunnerDenied,
    RunnerProfile,
    provider_environment,
    sanitize_rtk,
    validate_git_request,
    write_rtk_metrics,
)
from runner.entrypoint import validate_request as validate


@pytest.fixture
def runner_config(tmp_path: Path) -> RunnerConfig:
    home = tmp_path / "home"
    cli_home = home / ".claude"
    project = tmp_path / "projects" / "allowed"
    cli_home.mkdir(parents=True)
    project.mkdir(parents=True)
    return RunnerConfig(
        user_id=100,
        unix_user="ha-ilya",
        home=home.resolve(),
        path="/usr/local/bin:/usr/bin:/bin",
        accounts={
            "claude-main": RunnerProfile(
                provider="claude_code",
                cli_home=cli_home.resolve(),
                metrics_file=tmp_path / "metrics" / "claude.json",
            )
        },
        project_roots=(project.resolve(),),
        git_allowed_hosts=("github.com",),
    )


def test_validate_request_accepts_only_exact_identity_profile_and_project(
    runner_config: RunnerConfig,
) -> None:
    cli_home = runner_config.accounts["claude-main"].cli_home
    project = runner_config.project_roots[0]

    resolved_home, resolved_cwd = validate(
        runner_config,
        user_id=100,
        provider="claude_code",
        account="claude-main",
        cli_home=str(cli_home),
        cwd=str(project),
        command=["claude", "--print"],
    )

    assert resolved_home == cli_home
    assert resolved_cwd == project


@pytest.mark.parametrize(
    "field", ["user", "provider", "account", "home", "cwd", "command", "absolute_command"]
)
def test_validate_request_fails_closed(runner_config: RunnerConfig, field: str) -> None:
    values = {
        "user_id": 100,
        "provider": "claude_code",
        "account": "claude-main",
        "cli_home": str(runner_config.accounts["claude-main"].cli_home),
        "cwd": str(runner_config.project_roots[0]),
        "command": ["claude"],
    }
    if field == "user":
        values["user_id"] = 200
    elif field == "provider":
        values["provider"] = "codex"
    elif field == "account":
        values["account"] = "foreign"
    elif field == "home":
        values["cli_home"] = str(runner_config.home)
    elif field == "cwd":
        values["cwd"] = str(runner_config.home)
    elif field == "command":
        values["command"] = ["bash"]
    else:
        values["command"] = ["/tmp/claude"]

    with pytest.raises(RunnerDenied):
        validate(runner_config, **values)  # type: ignore[arg-type]


def test_provider_environment_contains_no_application_secrets(
    runner_config: RunnerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")

    environment = provider_environment(
        runner_config, "claude_code", runner_config.accounts["claude-main"].cli_home
    )

    assert environment["CLAUDE_CONFIG_DIR"] == str(runner_config.accounts["claude-main"].cli_home)
    assert environment["RTK_TELEMETRY_DISABLED"] == "1"
    assert "TELEGRAM_BOT_TOKEN" not in environment


def test_runner_sanitizes_rtk_history_and_raw_tee(runner_config: RunnerConfig) -> None:
    cli_home = runner_config.accounts["claude-main"].cli_home
    runtime = cli_home / ".rtk"
    tee = runtime / "tee"
    tee.mkdir(parents=True)
    (tee / "raw.log").write_text("secret", encoding="utf-8")
    database = runtime / "history.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE commands (
               timestamp TEXT, original_cmd TEXT, rtk_cmd TEXT, project_path TEXT,
               input_tokens INTEGER, output_tokens INTEGER, saved_tokens INTEGER)"""
        )
        connection.execute(
            """INSERT INTO commands VALUES (
               datetime('now'), 'git status --secret', 'rtk git status --secret',
               '/private', 100, 20, 80)"""
        )

    sanitize_rtk(cli_home)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT original_cmd,rtk_cmd,project_path FROM commands"
        ).fetchone()
    assert row == ("git", "rtk git", "")
    assert not any(tee.iterdir())

    metrics_file = runner_config.accounts["claude-main"].metrics_file
    write_rtk_metrics(cli_home, metrics_file)
    payload = metrics_file.read_text(encoding="utf-8")
    assert '"commands":1' in payload
    assert "git" not in payload
    assert "private" not in payload


def test_git_request_allowlist_accepts_status_and_safe_clone(runner_config: RunnerConfig) -> None:
    root = runner_config.project_roots[0]

    assert (
        validate_git_request(
            runner_config,
            user_id=100,
            cwd=str(root),
            command=["git", "status", "--short", "--branch"],
        )
        == root
    )
    assert (
        validate_git_request(
            runner_config,
            user_id=100,
            cwd=str(root),
            command=["git", "push", "--dry-run", "origin", "HEAD"],
        )
        == root
    )
    assert (
        validate_git_request(
            runner_config,
            user_id=100,
            cwd=str(root),
            command=[
                "git",
                "clone",
                "--",
                "https://github.com/example/repo.git",
                str(root / "repo"),
            ],
        )
        == root
    )


@pytest.mark.parametrize(
    "command",
    [
        ["git", "-C", "/etc", "status"],
        ["git", "config", "--global", "credential.helper", "evil"],
        ["git", "clone", "--", "http://github.com/example/repo.git", "repo"],
        ["git", "push", "evil", "HEAD"],
        ["git", "push", "--dry-run", "evil", "HEAD"],
        ["git", "push", "--dry-run", "origin", "main"],
    ],
)
def test_git_request_allowlist_rejects_arbitrary_commands(
    runner_config: RunnerConfig, command: list[str]
) -> None:
    with pytest.raises(RunnerDenied):
        validate_git_request(
            runner_config, user_id=100, cwd=str(runner_config.project_roots[0]), command=command
        )
