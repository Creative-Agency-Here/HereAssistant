"""/deploy — самоперезапуск процесса, отчёт после старта."""

import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from core import config, db, events, version
from .common import is_admin

router = Router()
log = logging.getLogger("bridge.deploy")


def _bump_restart_counter() -> int:
    """Увеличить счётчик перезапусков за сегодня. Возвращает новый номер."""
    f = config.STATE_DIR / "restart_count.json"
    today = datetime.date.today().isoformat()
    data = {"date": today, "count": 0}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    data["count"] = int(data.get("count", 0)) + 1
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data["count"]


@router.message(Command("deploy"))
async def cmd_deploy(message: Message):
    if not is_admin(message):
        return

    v_before = version.bot_version()
    old_text = config.BOT_FILE.read_text(encoding="utf-8", errors="replace") if config.BOT_FILE.exists() else ""

    # резервная копия
    backup_path = version.backup_current_bot()

    # сохранить состояние для постстарта
    state = {
        "timestamp_before": time.time(),
        "chat_id": message.chat.id,
        "thread_id": message.message_thread_id or 0,
        "hash_before": v_before["hash"],
        "text_before": old_text,
        "backup": str(backup_path) if backup_path else None,
    }
    config.RESTART_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.RESTART_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    events.log("deploy_initiated",
               user_id=message.from_user.id, chat_id=message.chat.id,
               payload={"version_before": v_before["short"]})

    await message.answer(
        f"🔄 Перезапуск запрошен — выполню, как только закончу текущие задачи "
        f"(чтобы не оборвать ответ на полуслове).\n"
        f"Текущая версия: {v_before['short']}\n"
        f"Резервная копия: {backup_path.name if backup_path else '—'}"
    )

    # НЕ рестартим напрямую. Пишем файл-запрос. Единственный исполнитель execv —
    # restart_watcher в bot.py: он дождётся is_busy()==False (финал отправлен),
    # пришлёт сигнал «🔄 Перезапускаю» и только потом выполнит рестарт.
    config.RESTART_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.RESTART_REQUEST_FILE.write_text(
        json.dumps({
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id or 0,
            "reason": "обновление кода (/deploy)",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("restart requested via /deploy (watcher выполнит после тишины)")


async def post_restart_report(bot: Bot) -> bool:
    """Вызвать при старте бота. Если есть restart.json — рапортовать в Telegram.

    Возвращает True, если отчёт был отправлен (тогда startup_notification
    можно пропустить, чтобы не дублировать).
    """
    if not config.RESTART_STATE_FILE.exists():
        return False
    try:
        state = json.loads(config.RESTART_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        config.RESTART_STATE_FILE.unlink(missing_ok=True)
        return False

    duration = time.time() - state.get("timestamp_before", time.time())
    v_after = version.project_version()
    restart_n = _bump_restart_counter()

    # Сравнить старый snapshot_full с текущим состоянием — получим список изменённых файлов.
    # Загружаем снимок ДО того как save_snapshot() в bot.py его перезапишет.
    old_snap = version.load_snapshot_full()
    changes = version.project_changes(old_snap)
    old_hash = (old_snap or {}).get("project_hash", "")

    msg_lines = [
        f"✓ Перезапуск №{restart_n} за сегодня — за {duration:.1f} сек",
        f"Версия: {version.short(old_hash)} → {v_after['short']} ({v_after['files']} файлов)",
    ]
    if changes:
        total_a = sum(ch.get("added", 0) for ch in changes)
        total_r = sum(ch.get("removed", 0) for ch in changes)
        msg_lines.append(f"\nИзменено файлов: {len(changes)} (всего +{total_a} −{total_r} строк)")
        for ch in changes[:15]:
            mark = {"modified": "✎", "added": "+", "removed": "-"}.get(ch["kind"], "?")
            msg_lines.append(f"  {mark} {ch['file']}  +{ch['added']} −{ch['removed']}")
        if len(changes) > 15:
            msg_lines.append(f"  … ещё {len(changes) - 15}")
    else:
        msg_lines.append("Изменений в коде не было")
    msg_lines.append(f"\nПоследнее изменение: {v_after['mtime']}")

    try:
        chat_id = state.get("chat_id")
        thread_id = state.get("thread_id") or None
        if chat_id:
            await bot.send_message(chat_id, "\n".join(msg_lines),
                                   message_thread_id=thread_id if thread_id else None)
    except Exception as e:
        log.warning("Не смог отправить post-restart report: %s", e)

    events.log("deploy_completed",
               chat_id=state.get("chat_id"),
               duration_ms=int(duration * 1000),
               payload={"version_before": version.short(old_hash),
                        "version_after": v_after["short"],
                        "restart_n_today": restart_n,
                        "changes": [c["file"] for c in changes]})

    config.RESTART_STATE_FILE.unlink(missing_ok=True)
    return True


async def startup_notification(bot: Bot):
    """Короткое «✓ Бот запущен» в последний активный тред.

    Срабатывает при ЛЮБОМ старте (PM2-перезапуск, ручной запуск, падение и подъём)
    — в отличие от post_restart_report, который шлёт детальный отчёт только если
    был запрошен /deploy или self-restart.
    """
    try:
        # последний активный диалог из БД
        with db.conn() as c:
            row = c.execute(
                "SELECT chat_id, thread_id FROM conversations "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return
        chat_id = row["chat_id"]
        thread_id = row["thread_id"] or None

        v = version.project_version()
        text = (
            f"✓ Бот запущен — {v['short']} ({v['files']} файлов)\n"
            f"Время старта: {datetime.datetime.now().strftime('%H:%M:%S')}"
        )
        await bot.send_message(chat_id, text,
                               message_thread_id=thread_id if thread_id else None)
    except Exception as e:
        log.warning("startup notification failed: %s", e)
