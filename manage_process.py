"""Process и CLI-auth helpers менеджера."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

LOGIN_STATE_INACCESSIBLE = "inaccessible"


@dataclass(frozen=True, slots=True)
class BotProcessState:
    running: bool
    pid: int | None = None
    uptime_minutes: int | None = None


def bot_process_state(lock_file: Path) -> BotProcessState:
    """Читает single-instance lock без запуска PM2 и изменения состояния."""
    try:
        raw = lock_file.read_text(encoding="utf-8").strip()
        pid_raw, *timestamp_raw = raw.split("|", 1)
        pid = int(pid_raw)
        started_at = float(timestamp_raw[0]) if timestamp_raw else 0.0
        if pid <= 0:
            return BotProcessState(False)
        os.kill(pid, 0)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return BotProcessState(False)
    except PermissionError:
        # Процесс существует, но текущему Unix-пользователю запрещён signal 0.
        pass
    except OSError:
        return BotProcessState(False)
    uptime = max(0, int((time.time() - started_at) / 60)) if started_at else None
    return BotProcessState(True, pid=pid, uptime_minutes=uptime)


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def run_visible(argv: Sequence[str], env_extra: Mapping[str, str] | None = None) -> int:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.call(list(argv), env=env)


def npm_install_argv(package: str, *, npm_path: str, windows: bool) -> list[str]:
    if windows and npm_path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", "npm", "install", "-g", package]
    return ["npm", "install", "-g", package]


def install_npm_package(package: str) -> bool:
    npm_path = shutil.which("npm")
    if npm_path is None:
        return False
    argv = npm_install_argv(package, npm_path=npm_path, windows=os.name == "nt")
    return run_visible(argv) == 0


def login_markers(provider_key: str, cli_home: Path) -> tuple[Path, ...]:
    if provider_key == "claude_code":
        return (
            cli_home / ".credentials.json",
            cli_home / "credentials.json",
            cli_home / ".claude" / ".credentials.json",
        )
    if provider_key == "codex":
        return (cli_home / "auth.json", cli_home / ".codex" / "auth.json")
    if provider_key == "gemini":
        return (
            cli_home / ".gemini" / "oauth_creds.json",
            cli_home / ".gemini" / "credentials.json",
        )
    if provider_key == "qwen_code":
        return (cli_home / ".qwen" / "settings.json",)
    return ()


def login_state(provider_key: str, cli_home: Path) -> tuple[bool, str]:
    try:
        if not cli_home.exists():
            return False, ""
        for marker in login_markers(provider_key, cli_home):
            if marker.exists():
                if provider_key == "gemini":
                    return True, f"{marker.parent.name}/{marker.name}"
                return True, marker.name
    except OSError:
        # Изолированные OS-runner профили намеренно закрыты от control plane.
        return False, LOGIN_STATE_INACCESSIBLE
    return False, ""
