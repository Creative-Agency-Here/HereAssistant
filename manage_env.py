"""Typed .env helpers менеджера без побочных эффектов импорта."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict


class EnvState(TypedDict):
    exists: bool
    token_set: bool
    admin_set: bool
    claim_pending: bool


def env_template(*, default_cwd: Path | None = None) -> str:
    cwd = default_cwd or Path.home()
    return (
        "# Telegram-токен (новый, после revoke у @BotFather)\n"
        "TELEGRAM_BOT_TOKEN=PASTE_HERE\n\n"
        "# Telegram user_id админа. Можно оставить пустым —\n"
        "# тогда при первом запуске бота в консоли появится\n"
        "# claim-ссылка. Откроешь её — бот сам впишет твой id сюда.\n"
        "ADMIN_TELEGRAM_ID=\n\n"
        f"DEFAULT_CWD={cwd}\n"
        "CLI_TIMEOUT_SEC=1800\n"
        "MAX_HISTORY=20\n\n"
        "# --- Claude Code ---\n"
        "# Что разрешено CLI без подтверждения: acceptEdits | default\n"
        "CLAUDE_PERMISSION_MODE=acceptEdits\n"
        "CLAUDE_DEBUG_STREAM=0\n\n"
        "# --- Прогресс-стриминг ---\n"
        "PROGRESS_ENABLED=1\n"
        "PROGRESS_MIN_INTERVAL_SEC=1.5\n"
        "TYPING_INTERVAL_SEC=4\n\n"
        "# --- Прерывание ---\n"
        "INTERRUPT_ON_NEW_MESSAGE=1\n"
    )


def ensure_env(path: Path) -> None:
    if not path.exists():
        path.write_text(env_template(), encoding="utf-8")


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def env_value(path: Path, key: str) -> str:
    return read_env(path).get(key, "").strip().strip('"').strip("'")


def admin_ids(path: Path) -> list[str]:
    values = read_env(path)
    raw = values.get("ADMIN_IDS") or values.get("ADMIN_TELEGRAM_ID", "")
    return [
        item
        for part in raw.replace(";", ",").split(",")
        if (item := part.strip()) and item != "PASTE_HERE" and item.lstrip("-").isdigit()
    ]


def env_state(path: Path) -> EnvState:
    values = read_env(path)
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    return {
        "exists": path.exists(),
        "token_set": bool(token) and token != "PASTE_HERE",
        "admin_set": bool(admin_ids(path)),
        "claim_pending": bool(values.get("CLAIM_CODE", "")),
    }
