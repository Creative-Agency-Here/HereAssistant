"""/stats, /log — статистика и события."""

import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from core import events
from .common import is_allowed

router = Router()


def _format_stats(title: str, data: dict) -> str:
    lines = [f"📊 {title}", "", f"Сообщений: {data['total_messages']}"]
    if data["by_model"]:
        lines.append("\nПо моделям:")
        for row in data["by_model"]:
            t_in = row["t_in"] or 0
            t_out = row["t_out"] or 0
            avg_ms = int(row["avg_ms"] or 0)
            label = f"{row['account_label']} · {row['model'] or row['provider']}"
            lines.append(f"  {label}: {row['msgs']} сообщ., "
                         f"{t_in}/{t_out} токенов in/out, ~{avg_ms} мс ср.")
    lines.append(f"\nОшибок: {data['errors']}")
    return "\n".join(lines)


@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject):
    if not is_allowed(message):
        return
    arg = (command.args or "").strip().lower()
    if arg == "week":
        data = events.stats_window(7 * 24 * 3600)
        await message.answer(_format_stats("Статистика за 7 дней", data))
    elif arg in ("all", "total"):
        # 10 лет — фактически всё
        data = events.stats_window(10 * 365 * 24 * 3600)
        await message.answer(_format_stats("Статистика за всё время", data))
    else:
        data = events.stats_window(24 * 3600)
        await message.answer(_format_stats("Статистика за 24 часа", data))


@router.message(Command("log"))
async def cmd_log(message: Message, command: CommandObject):
    if not is_allowed(message):
        return
    arg = (command.args or "").strip().lower()
    only_err = arg == "error"
    rows = events.recent(limit=20, only_errors=only_err, hours=24)
    if not rows:
        await message.answer("Событий за последние 24ч нет.")
        return
    lines = ["📜 Последние события:" if not only_err else "❌ Последние ошибки:"]
    for r in rows:
        ts = datetime.datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        kind = r["event_type"]
        bits = [ts, kind]
        if r["account_label"]:
            bits.append(r["account_label"])
        if r["model"]:
            bits.append(r["model"])
        if r["duration_ms"]:
            bits.append(f"{r['duration_ms']}ms")
        lines.append("  " + "  ".join(bits))
    await message.answer("\n".join(lines)[:4000])
