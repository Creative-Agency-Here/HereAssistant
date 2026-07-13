"""Dashboard/header renderer manager UI."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from manage_accounts import list_accounts
from manage_env import admin_ids, env_state, env_value
from manage_process import bot_process_state
from manage_ui import B, C, D, G, M, R, X, Y, box_bot, box_mid, box_top, logo

_BOT_CACHE: dict[str, object] = {"done": False, "username": None}


def bot_username(env_path: Path) -> str | None:
    if _BOT_CACHE["done"]:
        cached = _BOT_CACHE["username"]
        return str(cached) if cached else None
    _BOT_CACHE["done"] = True
    token = env_value(env_path, "TELEGRAM_BOT_TOKEN")
    if not token or token == "PASTE_HERE":
        return None
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getMe", timeout=4
        ) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, Mapping) or not payload.get("ok"):
        return None
    result = payload.get("result")
    if not isinstance(result, Mapping) or not result.get("username"):
        return None
    username = "@" + str(result["username"])
    _BOT_CACHE["username"] = username
    return username


def render_header(
    *,
    base_dir: Path,
    env_path: Path,
    db_path: Path,
) -> None:
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()
    logo()
    print(box_top())
    print(box_mid(f"{B}{M}HereAssistant{X}{D} — мульти-CLI Telegram-мост{X}"))
    print(box_mid(f"{D}проект: {base_dir}{X}"))
    print(f"{C}├{'─' * 62}┤{X}")

    state = env_state(env_path)
    username = bot_username(env_path)
    process = bot_process_state(base_dir / ".runtime" / "state" / "bot.lock")
    if process.running:
        process_text = f"{G}работает{X} {D}(PID {process.pid}){X}"
    else:
        process_text = f"{R}остановлен{X}"
    if username:
        print(box_mid(f"Бот        {G}{username}{X} · {process_text}"))
    elif state["token_set"]:
        print(box_mid(f"Бот        {G}токен есть{X} · {process_text}"))
    else:
        print(box_mid(f"Бот        {R}токен не задан{X} {D}— пункт «Настройки → .env»{X}"))

    owners = admin_ids(env_path)
    if owners:
        print(box_mid(f"Админ      {G}{', '.join(owners)}{X}"))
    else:
        print(box_mid(f"Админ      {Y}не задан{X} {D}(станет админом первый /start){X}"))

    accounts = list_accounts(db_path)
    if not accounts:
        print(box_mid(f"Аккаунты   {Y}нет{X} {D}— добавь пункт [2]{X}"))
    else:
        active = sum(bool(account["enabled"]) for account in accounts)
        disabled = len(accounts) - active
        disabled_text = f" · {D}отключено: {disabled}{X}" if disabled else ""
        print(
            box_mid(f"Аккаунты   {G}включено: {active}{X}{disabled_text} · {D}подробности [1]{X}")
        )
    print(box_bot())


def reset_bot_cache() -> None:
    _BOT_CACHE.update(done=False, username=None)
