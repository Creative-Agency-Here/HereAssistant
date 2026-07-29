"""Установка блокирующего PreToolUse-хука в auth home аккаунта Claude.

Хук — единственное место, где шлюз может остановить разрушительную команду до
её выполнения. Всё остальное в проекте только наблюдает.

Границы честно: работает лишь у Claude Code (у Gemini и Codex механизма нет,
у Qwen контракт не проверен), блокирует только катастрофический уровень и
обходится любой динамикой вроде `base64 -d | sh`. Подробности — в SECURITY.md.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

HOOK_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "claude_risk_hook.py"
# Хук обязан отвечать мгновенно: при таймауте Claude выполняет команду (fail-open),
# поэтому запас времени маленький и осмысленный только для холодного старта Python.
HOOK_TIMEOUT_SEC = 10
HOOK_MARKER = "claude_risk_hook.py"


def hook_command(python: str | Path | None = None) -> str:
    """Строка запуска хука с экранированием.

    Кавычки обязательны: путь к проекту может содержать пробелы, и без них
    Claude просто не запустит хук — молча, без ошибки и без защиты.
    Проверено на пути `.../Visual Studio Code/Creative Agency Here/...`.
    """
    interpreter = str(python or sys.executable)
    return f"{shlex.quote(interpreter)} {shlex.quote(str(HOOK_SCRIPT))}"


def configure_claude_hook(cli_home: str | Path, *, python: str | Path | None = None) -> bool:
    """Идемпотентно подключает блокирующий хук к профилю Claude аккаунта.

    Возвращает True, если после вызова хук установлен (в том числе если он уже
    был). Чужие хуки и настройки сохраняются.
    """
    home = Path(cli_home)
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return False
    settings = home / "settings.json"
    try:
        payload = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False

    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return False
    pre_tool = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre_tool, list):
        return False

    command = hook_command(python)
    already = any(
        HOOK_MARKER in str(hook.get("command", ""))
        for entry in pre_tool
        if isinstance(entry, dict)
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    )
    if not already:
        pre_tool.append(
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SEC}],
            }
        )

    temporary = settings.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(settings)
        home.chmod(0o700)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def is_configured(cli_home: str | Path) -> bool:
    """Установлен ли хук в профиле аккаунта."""
    settings = Path(cli_home) / "settings.json"
    try:
        payload = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    hooks = payload.get("hooks")
    entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    if not isinstance(entries, list):
        return False
    return any(
        HOOK_MARKER in str(hook.get("command", ""))
        for entry in entries
        if isinstance(entry, dict)
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    )
