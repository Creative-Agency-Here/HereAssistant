"""Read-only terminal views менеджера."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from manage_accounts import ProviderSpec, list_accounts
from manage_audit import format_timestamp, format_tokens, ssh_history, telegram_history
from manage_process import login_state
from manage_ui import BG_G, BG_R, BG_Y, BLACK, B, C, D, G, M, R, X, Y, badge, line


def show_history(db_path: Path) -> None:
    print(f"\n{B}{M}История · Аудит{X}")
    print(f"\n{B}Обращения в Telegram{X} {D}(кто · когда · какой аккаунт){X}")
    print(line(width=64))
    rows = telegram_history(db_path)
    if not rows:
        print(f"  {D}пока пусто — боту ещё не писали{X}")
    labels = {
        "message_in": f"{C}→ запрос{X}",
        "message_out": f"{G}✓ ответ{X}",
        "error": f"{R}✗ ошибка{X}",
    }
    for row in rows:
        event = labels.get(row["event_type"], row["event_type"])
        account = row["account_label"] or "-"
        tokens = f" · {format_tokens(row['tokens'])} ток" if row["tokens"] else ""
        print(
            f"  {D}{format_timestamp(row['timestamp'])}{X}  {event:<18} "
            f"user {B}{row['user_id']}{X}  {M}{account}{X}{D}{tokens}{X}"
        )
    print(f"\n{B}SSH-заходы на сервер{X} {D}(юзер · IP · когда){X}")
    print(line(width=64))
    entries = ssh_history()
    if not entries:
        print(f"  {D}команда last недоступна на этой системе{X}")
    for entry in entries:
        print(f"  {D}{entry}{X}")


def show_accounts(
    db_path: Path,
    providers: Mapping[str, ProviderSpec | Mapping[str, Any]],
):
    rows = list_accounts(db_path)
    print(f"\n{B}Зарегистрированные аккаунты{X}")
    print(line())
    if not rows:
        print(f"  {Y}пока нет — добавь через пункт меню 2{X}")
        return rows
    print(
        f"  {B}{'#':<3} {'LABEL':<20} {'PROVIDER':<14} {'MODEL':<22} {'LOGIN':<10} {'NOTE':<20}{X}"
    )
    print(line(width=64))
    for row in rows:
        provider = next(
            (item for item in providers.values() if item["key"] == row["provider"]), None
        )
        if provider:
            logged, _ = login_state(str(provider["key"]), Path(row["cli_home_path"]))
            login = badge("есть", BLACK, BG_G) if logged else badge("нет ", BLACK, BG_R)
        else:
            login = "?"
        model = str(row["default_model"] or "-")[:22]
        note = str(row["notes"] or "")[:20]
        print(
            f"  {row['id']:<3} {row['label']:<20} {row['provider']:<14} "
            f"{model:<22} {login:<18} {D}{note}{X}"
        )
    return rows


def show_disk_state(cli_homes_dir: Path, db_path: Path) -> None:
    print(f"\n{B}Папки в .runtime/cli_homes/{X}")
    print(line())
    if not cli_homes_dir.exists() or not any(cli_homes_dir.iterdir()):
        print(f"  {Y}пусто{X}")
        return
    registered = {Path(row["cli_home_path"]).resolve() for row in list_accounts(db_path)}
    for item in sorted(cli_homes_dir.iterdir()):
        if not item.is_dir():
            continue
        marker = (
            badge("зареган", BLACK, BG_G)
            if item.resolve() in registered
            else badge("сирота ", BLACK, BG_Y)
        )
        provider = item.name.split("__", 1)[0] if "__" in item.name else "?"
        try:
            files: int | str = sum(1 for path in item.rglob("*") if path.is_file())
        except OSError:
            files = "?"
        print(f"  {marker}  {B}{item.name}{X}  {D}({files} файлов, {provider}){X}")
