import os
from pathlib import Path

import pytest

from core import config
from providers.os_runner import ProcessBoundary, RunnerConfigurationError


def account(**overrides: object) -> dict[str, object]:
    return {
        "provider": "claude_code",
        "label": "claude-main",
        "cli_home_path": "/home/ha-ilya/.claude",
        "owner_user_id": 100,
        "shared": 0,
        **overrides,
    }


def enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "OS_RUNNERS_ENABLED", True)
    monkeypatch.setattr(config, "OS_RUNNER_MAP", {100: "ha-ilya"})
    monkeypatch.setattr(config, "OS_RUNNER_EXECUTABLE", "/usr/local/libexec/hereassistant-runner")


@pytest.mark.skipif(os.name == "nt", reason="Unix runner wrapper")
def test_runner_wraps_private_owned_account_without_application_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "must-not-cross-boundary")
    boundary = ProcessBoundary(account(), 100)

    prepared = boundary.prepare(["claude", "--print"], "/srv/ilya/project", "claude_code")

    assert prepared.cwd is None
    assert prepared.argv[:7] == [
        "/usr/bin/sudo",
        "-n",
        "-H",
        "-u",
        "ha-ilya",
        "--",
        "/usr/local/libexec/hereassistant-runner",
    ]
    assert prepared.argv[-3:] == ["--", "claude", "--print"]
    assert "TELEGRAM_BOT_TOKEN" not in prepared.env


@pytest.mark.skipif(os.name == "nt", reason="Unix runner wrapper")
@pytest.mark.parametrize(
    ("current", "user_id", "message"),
    [
        (account(owner_user_id=200), 100, "private provider account"),
        (account(shared=1), 100, "private provider account"),
        (account(), None, "user_id"),
    ],
)
def test_runner_rejects_ambiguous_identity(
    monkeypatch: pytest.MonkeyPatch,
    current: dict[str, object],
    user_id: int | None,
    message: str,
) -> None:
    enable(monkeypatch)

    with pytest.raises(RunnerConfigurationError, match=message):
        ProcessBoundary(current, user_id)


@pytest.mark.skipif(os.name == "nt", reason="Unix runner wrapper")
def test_runner_requires_explicit_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    enable(monkeypatch)
    monkeypatch.setattr(config, "OS_RUNNER_MAP", {})

    with pytest.raises(RunnerConfigurationError, match="RUNNER_NOT_CONFIGURED"):
        ProcessBoundary(account(), 100)


def test_disabled_runner_preserves_current_process_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "OS_RUNNERS_ENABLED", False)
    boundary = ProcessBoundary(account(cli_home_path=str(tmp_path / "home")), 100)

    prepared = boundary.prepare(["claude"], str(tmp_path), "claude_code")

    assert prepared.argv == ["claude"]
    assert prepared.cwd == str(tmp_path)
