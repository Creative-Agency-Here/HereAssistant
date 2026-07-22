#!/usr/bin/env python3
"""CLI и hook-entrypoint единого native-session коннектора HereAssistant."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aiohttp  # noqa: E402

from core import crm_sync, db, native_hooks, native_sessions  # noqa: E402

_MAX_STDIN_BYTES = 1024 * 1024


def _providers(value: str) -> list[str] | None:
    if value.strip().lower() == "all":
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _hook_response(system_message: str | None = None) -> None:
    response: dict[str, Any] = {"continue": True}
    if system_message:
        response["systemMessage"] = system_message
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")


def _read_hook_payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    if len(raw) > _MAX_STDIN_BYTES:
        raise ValueError("hook input слишком большой")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("hook input должен быть JSON object")
    return payload


async def _flush_best_effort() -> None:
    if not crm_sync.configured():
        return
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await crm_sync.flush_once(session)


def hook(provider: str) -> int:
    """Hook не ломает ход агента даже при ошибке локального bridge."""
    try:
        payload = _read_hook_payload()
        db.init()
        result = native_sessions.ingest_hook(provider, payload)
        if result.state == "queued":
            try:
                asyncio.run(_flush_best_effort())
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                pass
        if result.state == "missing-user":
            _hook_response(
                "HereAssistant: укажи HEREASSISTANT_NATIVE_USER_ID в .env для CRM-атрибуции."
            )
        elif result.state == "enqueue-failed":
            _hook_response("HereAssistant: не удалось поставить AI-сессию в outbox.")
        else:
            _hook_response()
    except (json.JSONDecodeError, UnicodeError, ValueError):
        _hook_response("HereAssistant: native hook получил некорректный input.")
    except (OSError, RuntimeError, sqlite3.Error, TypeError):
        _hook_response("HereAssistant: native hook завершился с локальной ошибкой.")
    return 0


def _print_status() -> None:
    connector = native_sessions.connector_status()
    print("\nHereAssistant · AI-сессии → HereCRM")
    print(f"  HereCRM connector: {'готов' if connector['configured'] else 'не настроен'}")
    print("  Native user: " + ("настроен" if connector["nativeUserConfigured"] else "не настроен"))
    print(f"  Outbox: {connector['pending']} ожидает")
    labels = {
        "current": "подключён",
        "disabled": "не подключён",
        "outdated": "нужно обновить",
        "invalid": "ошибка JSON",
    }
    for state in native_hooks.inspect():
        cli = "CLI найден" if state.cli_found else "CLI не найден"
        print(f"  {state.title:<12} {labels[state.state]}, {cli}")


def _change_hooks(enabled: bool, clients: str) -> int:
    providers = _providers(clients)
    changed = native_hooks.install(providers) if enabled else native_hooks.uninstall(providers)
    action = "обновлён" if enabled else "удалён"
    for provider, was_changed in changed.items():
        title = native_hooks.CLIENTS[provider].title
        suffix = action if was_changed else "уже актуален"
        print(f"{title}: {suffix}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Native AI sessions → HereAssistant → HereCRM")
    commands = result.add_subparsers(dest="command", required=True)
    hook_parser = commands.add_parser("hook", help="вызов из CLI hook")
    hook_parser.add_argument("--provider", required=True, choices=native_sessions.PROVIDERS)
    commands.add_parser("status", help="безопасный статус интеграции")
    for name in ("install", "uninstall"):
        item = commands.add_parser(name, help=f"{name} native hooks")
        item.add_argument(
            "--clients",
            default="all",
            help="all или claude_code,codex,qwen_code,gemini",
        )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "hook":
        return hook(args.provider)
    if args.command == "status":
        db.init()
        _print_status()
        return 0
    return _change_hooks(args.command == "install", args.clients)


if __name__ == "__main__":
    raise SystemExit(main())
