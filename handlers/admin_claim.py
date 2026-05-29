"""Назначение админа через claim-код."""

import logging
import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from core import config, db, events
from .common import is_admin, send_long

router = Router()
log = logging.getLogger("bridge.claim")


def _persist_admin_id(uid: int):
    config.append_env("ADMIN_TELEGRAM_ID", str(uid))


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    # Если админ ещё не назначен — режим клейма
    if config.ADMIN_ID is None:
        provided = (command.args or "").strip()
        if provided != config.CLAIM_CODE:
            await message.answer(
                "Этот бот ещё не привязан к админу.\n"
                "Чтобы стать админом, отправь:\n"
                "  /start <claim-код>\n\n"
                "Код выведен в консоли, где запущен bot.py."
            )
            return
        config.ADMIN_ID = message.from_user.id
        _persist_admin_id(config.ADMIN_ID)
        with db.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users(telegram_id, username, role, created_at) VALUES (?, ?, 'admin', ?)",
                (config.ADMIN_ID, message.from_user.username, int(time.time())),
            )
        events.log("admin_claim",
                   user_id=config.ADMIN_ID, chat_id=message.chat.id,
                   payload={"username": message.from_user.username})
        await message.answer(
            f"✓ Готово, ты админ. id={config.ADMIN_ID}\n\n"
            "Команды:\n"
            "/help — справка\n"
            "/accounts — аккаунты\n"
            "/status — что сейчас активно"
        )
        log.info("Admin claimed by user_id=%s username=%s",
                 config.ADMIN_ID, message.from_user.username)
        return

    if not is_admin(message):
        await message.answer(f"Доступ запрещён. id={message.from_user.id}")
        return

    await send_long(message,
        "Мульти-CLI мост готов.\n\n"
        "Набери / чтобы увидеть список команд, или /help — подробнее."
    )
