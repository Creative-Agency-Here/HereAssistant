"""Идемпотентная установка native hooks без затирания чужих настроек."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from . import config

MANAGED_HOOK_NAME = "hereassistant-native-session-sync"


@dataclass(frozen=True, slots=True)
class ClientSpec:
    provider: str
    title: str
    executable: str
    settings_parts: tuple[str, ...]
    event: str
    timeout: int


@dataclass(frozen=True, slots=True)
class HookState:
    provider: str
    title: str
    cli_found: bool
    state: str
    settings_path: Path


CLIENTS: dict[str, ClientSpec] = {
    "claude_code": ClientSpec(
        "claude_code", "Claude Code", "claude", (".claude", "settings.json"), "Stop", 30
    ),
    "codex": ClientSpec("codex", "Codex", "codex", (".codex", "hooks.json"), "Stop", 30),
    "qwen_code": ClientSpec(
        "qwen_code", "Qwen Code", "qwen", (".qwen", "settings.json"), "Stop", 30_000
    ),
    "gemini": ClientSpec(
        "gemini", "Gemini CLI", "gemini", (".gemini", "settings.json"), "AfterAgent", 30_000
    ),
}


def _home(home: str | Path | None = None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def _settings_path(spec: ClientSpec, home: str | Path | None = None) -> Path:
    return _home(home).joinpath(*spec.settings_parts)


def _script_path() -> Path:
    return config.BASE_DIR / "scripts" / "native_connector.py"


def _command(provider: str, python_executable: str | None = None) -> str:
    argv = [
        python_executable or sys.executable,
        str(_script_path()),
        "hook",
        "--provider",
        provider,
    ]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _group(spec: ClientSpec, python_executable: str | None = None) -> dict[str, Any]:
    return {
        "hooks": [
            {
                "type": "command",
                "command": _command(spec.provider, python_executable),
                "name": MANAGED_HOOK_NAME,
                "timeout": spec.timeout,
                "statusMessage": "HereAssistant: синхронизация AI-сессии…",
            }
        ]
    }


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ожидался JSON object")
    return raw


def _is_managed(group: object) -> bool:
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    return any(
        isinstance(hook, dict) and hook.get("name") == MANAGED_HOOK_NAME for hook in group["hooks"]
    )


def _without_managed(groups: object) -> list[object]:
    return [group for group in groups if not _is_managed(group)] if isinstance(groups, list) else []


def _next_settings(
    settings: dict[str, Any],
    spec: ClientSpec,
    *,
    enabled: bool,
    python_executable: str | None = None,
) -> dict[str, Any]:
    next_settings = dict(settings)
    raw_hooks = settings.get("hooks")
    hooks: dict[str, Any] = (
        {str(key): value for key, value in raw_hooks.items()} if isinstance(raw_hooks, dict) else {}
    )
    groups = _without_managed(hooks.get(spec.event))
    if enabled:
        groups.append(_group(spec, python_executable))
    if groups:
        hooks[spec.event] = groups
    else:
        hooks.pop(spec.event, None)
    if hooks:
        next_settings["hooks"] = hooks
    else:
        next_settings.pop("hooks", None)
    return next_settings


def _backup(path: Path, backup_root: str | Path | None = None) -> Path | None:
    if not path.exists():
        return None
    root = (
        Path(backup_root).expanduser()
        if backup_root is not None
        else Path.home() / ".hereassistant" / "hook-backups"
    )
    target_dir = root / path.parent.name
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = target_dir / f"{path.name}.{timestamp}.bak"
    shutil.copy2(path, target)
    target.chmod(0o600)
    return target


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temp.chmod(0o600)
        temp.replace(path)
        path.chmod(0o600)
    except (OSError, TypeError, ValueError):
        temp.unlink(missing_ok=True)
        raise


def selected_clients(values: Iterable[str] | None = None) -> list[ClientSpec]:
    providers = list(values or CLIENTS)
    unknown = sorted(set(providers) - CLIENTS.keys())
    if unknown:
        raise ValueError(f"Неизвестные клиенты: {', '.join(unknown)}")
    return [CLIENTS[provider] for provider in providers]


def install(
    providers: Iterable[str] | None = None,
    *,
    home: str | Path | None = None,
    backup_root: str | Path | None = None,
    python_executable: str | None = None,
) -> dict[str, bool]:
    changed: dict[str, bool] = {}
    for spec in selected_clients(providers):
        path = _settings_path(spec, home)
        current = _read_settings(path)
        updated = _next_settings(current, spec, enabled=True, python_executable=python_executable)
        changed[spec.provider] = updated != current
        if updated != current:
            _backup(path, backup_root)
            _write_private_json(path, updated)
    return changed


def uninstall(
    providers: Iterable[str] | None = None,
    *,
    home: str | Path | None = None,
    backup_root: str | Path | None = None,
) -> dict[str, bool]:
    changed: dict[str, bool] = {}
    for spec in selected_clients(providers):
        path = _settings_path(spec, home)
        if not path.exists():
            changed[spec.provider] = False
            continue
        current = _read_settings(path)
        updated = _next_settings(current, spec, enabled=False)
        changed[spec.provider] = updated != current
        if updated != current:
            _backup(path, backup_root)
            _write_private_json(path, updated)
    return changed


def inspect(
    providers: Iterable[str] | None = None,
    *,
    home: str | Path | None = None,
    python_executable: str | None = None,
) -> list[HookState]:
    states: list[HookState] = []
    for spec in selected_clients(providers):
        path = _settings_path(spec, home)
        state = "disabled"
        try:
            settings = _read_settings(path)
            raw_hooks = settings.get("hooks")
            hooks: dict[str, Any] = raw_hooks if isinstance(raw_hooks, dict) else {}
            managed = [group for group in hooks.get(spec.event, []) if _is_managed(group)]
            if managed:
                state = (
                    "current"
                    if len(managed) == 1 and managed[0] == _group(spec, python_executable)
                    else "outdated"
                )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            state = "invalid"
        states.append(
            HookState(
                provider=spec.provider,
                title=spec.title,
                cli_found=shutil.which(spec.executable) is not None,
                state=state,
                settings_path=path,
            )
        )
    return states
