import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import runner.entrypoint as runner_entrypoint
from runner.entrypoint import (
    RunnerConfig,
    RunnerDenied,
    RunnerProfile,
    audit_git_configuration,
    git_environment,
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
        git_broker=False,
        git_credential_helper=None,
        git_vault_socket=None,
        git_database=None,
        gitea_oauth_apps={},
    )


@pytest.fixture
def git_runner_config(runner_config: RunnerConfig, tmp_path: Path) -> RunnerConfig:
    return replace(
        runner_config,
        unix_user="ha-ilya-git",
        home=(tmp_path / "git-home").resolve(),
        accounts={},
        git_broker=True,
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


def test_provider_and_git_modes_cannot_be_crossed(
    runner_config: RunnerConfig, git_runner_config: RunnerConfig
) -> None:
    with pytest.raises(RunnerDenied, match="только в отдельном broker"):
        validate_git_request(
            runner_config,
            user_id=100,
            cwd=str(runner_config.project_roots[0]),
            command=["git", "status", "--short", "--branch"],
        )
    with pytest.raises(RunnerDenied, match="не запускает provider"):
        validate(
            git_runner_config,
            user_id=100,
            provider="claude_code",
            account="claude-main",
            cli_home=str(runner_config.accounts["claude-main"].cli_home),
            cwd=str(git_runner_config.project_roots[0]),
            command=["claude"],
        )


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


def test_git_request_allowlist_accepts_status_and_safe_clone(
    git_runner_config: RunnerConfig,
) -> None:
    root = git_runner_config.project_roots[0]

    assert (
        validate_git_request(
            git_runner_config,
            user_id=100,
            cwd=str(root),
            command=["git", "status", "--short", "--branch"],
        )
        == root
    )
    assert (
        validate_git_request(
            git_runner_config,
            user_id=100,
            cwd=str(root),
            command=["git", "push", "--dry-run", "origin", "HEAD"],
        )
        == root
    )
    assert (
        validate_git_request(
            git_runner_config,
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
    git_runner_config: RunnerConfig, command: list[str]
) -> None:
    with pytest.raises(RunnerDenied):
        validate_git_request(
            git_runner_config,
            user_id=100,
            cwd=str(git_runner_config.project_roots[0]),
            command=command,
        )


def test_git_environment_resets_inherited_helpers_and_contains_no_app_secrets(
    git_runner_config: RunnerConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "must-not-cross-boundary")
    helper = tmp_path / "root-owned-helper"
    vault_socket = tmp_path / "vault.sock"
    configured = replace(
        git_runner_config,
        git_credential_helper=helper,
        git_vault_socket=vault_socket,
    )

    environment = git_environment(
        configured, configured.project_roots[0], ["git", "push", "origin", "HEAD"]
    )

    assert environment["GIT_CONFIG_COUNT"] == "7"
    assert environment["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert environment["GIT_CONFIG_VALUE_0"] == ""
    assert environment["GIT_CONFIG_VALUE_1"] == "/dev/null"
    assert environment["GIT_CONFIG_VALUE_2"] == "never"
    assert environment["GIT_CONFIG_VALUE_3"] == "always"
    assert environment["GIT_CONFIG_VALUE_4"] == "always"
    assert environment["GIT_CONFIG_VALUE_5"] == str(helper)
    assert environment["GIT_CONFIG_VALUE_6"] == "true"
    assert environment["HEREASSISTANT_GIT_VAULT_SOCKET"] == str(vault_socket)
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["HEREASSISTANT_GIT_ACCESS"] == "write"
    assert "TELEGRAM_BOT_TOKEN" not in environment


def test_git_environment_without_vault_is_public_only(
    git_runner_config: RunnerConfig,
) -> None:
    environment = git_environment(
        git_runner_config,
        git_runner_config.project_roots[0],
        ["git", "pull", "--ff-only"],
    )

    assert environment["GIT_CONFIG_COUNT"] == "5"
    assert environment["GIT_CONFIG_VALUE_0"] == ""
    assert environment["GIT_CONFIG_VALUE_1"] == "/dev/null"
    assert "HEREASSISTANT_GIT_VAULT_SOCKET" not in environment
    assert environment["HEREASSISTANT_GIT_ACCESS"] == "read"


def init_git_repository(config: RunnerConfig) -> Path:
    root = config.project_roots[0]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "--local", "remote.origin.url", "https://github.com/example/repo.git"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "--local", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
        cwd=root,
        check=True,
    )
    return root


def test_git_config_audit_accepts_minimal_safe_repository(
    git_runner_config: RunnerConfig,
) -> None:
    root = init_git_repository(git_runner_config)

    audit_git_configuration(git_runner_config, root, ["git", "status", "--short", "--branch"])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("filter.evil.process", "sh -c arbitrary"),
        ("include.path", "/tmp/foreign-config"),
        ("core.sshCommand", "sh -c arbitrary"),
        ("http.proxy", "https://proxy.example"),
        ("credential.helper", "store"),
    ],
)
def test_git_config_audit_rejects_executable_and_credential_keys(
    git_runner_config: RunnerConfig, key: str, value: str
) -> None:
    root = init_git_repository(git_runner_config)
    subprocess.run(["git", "config", "--local", key, value], cwd=root, check=True)

    with pytest.raises(RunnerDenied, match="config key запрещён"):
        audit_git_configuration(git_runner_config, root, ["git", "pull", "--ff-only"])


def test_git_config_audit_rejects_remote_outside_allowlist(
    git_runner_config: RunnerConfig,
) -> None:
    root = init_git_repository(git_runner_config)
    subprocess.run(
        ["git", "config", "--local", "remote.origin.url", "https://evil.example/repo.git"],
        cwd=root,
        check=True,
    )

    with pytest.raises(RunnerDenied, match="remote host запрещён"):
        audit_git_configuration(git_runner_config, root, ["git", "push", "origin", "HEAD"])


def test_git_config_audit_rejects_gitdir_outside_project_roots(
    git_runner_config: RunnerConfig, tmp_path: Path
) -> None:
    root = git_runner_config.project_roots[0]
    outside = tmp_path / "outside.git"
    subprocess.run(
        ["git", "init", "-q", "--separate-git-dir", str(outside), str(root)],
        check=True,
    )

    with pytest.raises(RunnerDenied, match="metadata выходит"):
        audit_git_configuration(git_runner_config, root, ["git", "status", "--short", "--branch"])


def test_credentialed_git_requires_immutable_control_files(
    git_runner_config: RunnerConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = init_git_repository(git_runner_config)
    configured = replace(
        git_runner_config,
        git_credential_helper=tmp_path / "helper",
        git_vault_socket=tmp_path / "vault.sock",
    )
    monkeypatch.setattr(runner_entrypoint, "_is_immutable_file", lambda _path: False)

    with pytest.raises(RunnerDenied, match="immutable metadata"):
        audit_git_configuration(configured, root, ["git", "push", "origin", "HEAD"])

    monkeypatch.setattr(runner_entrypoint, "_is_immutable_file", lambda _path: True)
    audit_git_configuration(configured, root, ["git", "push", "origin", "HEAD"])
