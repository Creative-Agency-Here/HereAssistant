"""Fail-closed boundary between HereAssistant core and per-user Unix runners."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core import config


class RunnerConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedProcess:
    argv: list[str]
    cwd: str | None
    env: dict[str, str]


def _account_value(account: Any, key: str, default: object = None) -> object:
    try:
        return account[key]
    except (IndexError, KeyError):
        return default


def _host_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Only variables needed to start sudo; application secrets never reach a runner."""
    current = source or os.environ
    allowed = (
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
    )
    return {key: current[key] for key in allowed if current.get(key)}


class ProcessBoundary:
    def __init__(self, account: Any, user_id: int | None):
        self.account = account
        self.user_id = user_id
        self.enabled = config.OS_RUNNERS_ENABLED
        self.unix_user: str | None = None
        if not self.enabled:
            return
        if os.name == "nt":
            raise RunnerConfigurationError("OS runners поддерживаются только на Unix")
        if user_id is None:
            raise RunnerConfigurationError("OS runner требует Telegram user_id")
        owner = _account_value(account, "owner_user_id")
        shared = bool(_account_value(account, "shared", 0))
        if shared or owner != user_id:
            raise RunnerConfigurationError(
                "OS runner разрешает только private provider account текущего владельца"
            )
        self.unix_user = config.OS_RUNNER_MAP.get(user_id)
        if not self.unix_user:
            raise RunnerConfigurationError(f"RUNNER_NOT_CONFIGURED для user_id={user_id}")
        executable = Path(config.OS_RUNNER_EXECUTABLE)
        if not executable.is_absolute():
            raise RunnerConfigurationError("OS_RUNNER_EXECUTABLE должен быть абсолютным путём")

    def prepare(self, argv: list[str], cwd: str, provider: str) -> PreparedProcess:
        if not self.enabled:
            return PreparedProcess(argv=list(argv), cwd=cwd, env=dict(os.environ))
        assert self.user_id is not None and self.unix_user is not None
        cli_home = str(_account_value(self.account, "cli_home_path", ""))
        if not cli_home or not Path(cli_home).is_absolute():
            raise RunnerConfigurationError("cli_home_path runner account должен быть абсолютным")
        wrapped = [
            "/usr/bin/sudo",
            "-n",
            "-H",
            "-u",
            self.unix_user,
            "--",
            config.OS_RUNNER_EXECUTABLE,
            "--user-id",
            str(self.user_id),
            "--provider",
            provider,
            "--cli-home",
            cli_home,
            "--cwd",
            cwd,
            "--",
            *argv,
        ]
        return PreparedProcess(argv=wrapped, cwd=None, env=_host_environment())
