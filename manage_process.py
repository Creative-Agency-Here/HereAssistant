"""Process и CLI-auth helpers менеджера."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


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
    return ()


def login_state(provider_key: str, cli_home: Path) -> tuple[bool, str]:
    if not cli_home.exists():
        return False, ""
    for marker in login_markers(provider_key, cli_home):
        if marker.exists():
            if provider_key == "gemini":
                return True, f"{marker.parent.name}/{marker.name}"
            return True, marker.name
    return False, ""
