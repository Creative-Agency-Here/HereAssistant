"""Системные команды: /status, /version, /help, /new, /reset, /delete."""

import logging

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (Message, CallbackQuery,
                            InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo)
from aiogram.exceptions import TelegramBadRequest

from core import access, config, db, version
from . import repo
from .common import is_allowed

log = logging.getLogger(__name__)

router = Router()


@router.message(Command("web"))
async def cmd_web(message: Message):
    """Кнопка-вход в веб-интерфейс ассистента (Telegram Mini App)."""
    if not is_allowed(message):
        return
    if not config.WEBAPP_URL:
        await message.answer("WEBAPP_URL не задан в .env — открыть нечего.")
        return
    web_url = config.WEBAPP_URL + (
        f"/?key={config.WEBAPP_ACCESS_KEY}" if config.WEBAPP_ACCESS_KEY else "")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🖥 Открыть ассистента",
                             web_app=WebAppInfo(url=web_url))
    ]])
    await message.answer(
        "Веб-интерфейс ассистента: текущая задача, история диалогов, "
        "прогресс в реальном времени.",
        reply_markup=kb,
    )


HELP_TEXT = """\
HereAssistant — справка

Управление аккаунтами:
  /accounts         — список аккаунтов (кнопки для переключения)
  /account use X    — переключить на аккаунт X в этом чате
  /model            — список популярных моделей (кнопки)
  /model NAME       — переключить модель

Работа с папкой:
  /cwd              — показать текущую папку
  /cwd /path        — сменить рабочую папку
  /where            — текущая папка и проект
  /project list     — список проектов в workspace/
  /project new X    — создать проект workspace/X и переключиться
  /project use X    — переключиться на существующий проект

Сессия и история:
  /new              — новая сессия (сбросить provider_session_id)
  /reset            — очистить историю текущего чата (с подтверждением)
  /delete           — удалить беседу целиком: запись в БД + топик в Telegram (если в форум-топике)
  /status           — что сейчас активно (аккаунт, модель, cwd, кол-во сообщений)

Статистика:
  /stats            — сводка за 24 часа
  /stats week       — за 7 дней
  /stats all        — за всё время
  /log              — последние 20 событий
  /log error        — последние ошибки

Команда и доступ (для админов):
  /users            — все, кто писал боту: роли и допуск кнопками
  /users <поиск>    — поиск по нику, имени или id
  /access           — режим доступа: открытый / по подтверждению / только админы
  /logout           — снять свой доступ (владельцу — отвязать бота)

Деплой и версия:
  /version          — текущий хеш bot.py + дата
  /deploy           — перезапустить процесс (применить изменения в bot.py)
  /diff             — показать правки последнего ответа (по файлам, unified diff)

Файлы:
  Можно отправлять документы, фото, аудио, voice, видео —
  бот скачает их в .runtime/downloads/ и сообщит CLI пути.
"""


@router.message(Command("help"))
async def cmd_help(message: Message, command: CommandObject):
    if not is_allowed(message):
        return
    await message.answer(HELP_TEXT)


@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_allowed(message):
        return
    conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                    message.from_user.id)
    acc = repo.get_account(conv["account_id"]) if conv["account_id"] else None
    with db.conn() as c:
        msg_count = c.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (conv["id"],)
        ).fetchone()["n"]
    v = version.bot_version()
    lines = [
        f"chat={conv['chat_id']} thread={conv['thread_id']}",
        f"account: {acc['label'] if acc else '—'} ({acc['provider'] if acc else '—'})",
        f"model:   {conv['model'] or '—'}",
        f"session: {conv['provider_session_id'] or '(new)'}",
        f"cwd:     {conv['cwd']}",
        f"project: {conv['project_name'] or '—'}",
        f"history: {msg_count} сообщений",
        f"version: {v['short']} ({v['mtime']})",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("version"))
async def cmd_version(message: Message):
    if not is_allowed(message):
        return
    v = version.bot_version()
    await message.answer(f"bot.py\nhash: {v['short']} (полный: {v['hash'][:16]}...)\nmtime: {v['mtime']}")


@router.message(Command("new"))
async def cmd_new(message: Message):
    if not is_allowed(message):
        return
    conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                    message.from_user.id)
    repo.update_conv(conv["id"], provider_session_id=None)
    await message.answer("Новая сессия (session_id сброшен, история сохранена).")


def _reset_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, очистить", callback_data="reset:yes")],
        [InlineKeyboardButton(text="✗ Отмена", callback_data="reset:no")],
    ])


@router.message(Command("reset"))
async def cmd_reset(message: Message):
    if not is_allowed(message):
        return
    await message.answer("Очистить историю этого чата и сбросить сессию?",
                         reply_markup=_reset_keyboard())


@router.callback_query(F.data == "reset:yes")
async def cb_reset_yes(query: CallbackQuery):
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return
    conv = repo.get_or_create_conv(query.message.chat.id,
                                    query.message.message_thread_id or 0,
                                    query.from_user.id)
    with db.conn() as c:
        c.execute("DELETE FROM messages WHERE conversation_id=?", (conv["id"],))
    repo.update_conv(conv["id"], provider_session_id=None)
    await query.message.edit_text("История и сессия очищены.")
    await query.answer()


@router.callback_query(F.data == "reset:no")
async def cb_reset_no(query: CallbackQuery):
    await query.message.edit_text("Отменено.")
    await query.answer()


def _delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить беседу", callback_data="delete:yes")],
        [InlineKeyboardButton(text="✗ Отмена", callback_data="delete:no")],
    ])


@router.message(Command("delete"))
async def cmd_delete(message: Message):
    if not is_allowed(message):
        return
    chat_type = message.chat.type
    thread_id = message.message_thread_id or 0
    if thread_id and message.chat.is_forum:
        warn = "🗑 Беседа будет удалена из БД, а топик — удалён из Telegram."
    elif chat_type == "private":
        warn = ("🗑 Беседа будет удалена из БД.\n"
                "⚠️ В ЛС Telegram не позволяет боту удалить чат целиком — "
                "переписку придётся очистить вручную (Очистить историю).")
    else:
        warn = ("🗑 Беседа будет удалена из БД.\n"
                "⚠️ Топик/чат в Telegram удалить нельзя (это не форум-топик).")
    await message.answer(warn + "\n\nПродолжить?", reply_markup=_delete_keyboard())


@router.callback_query(F.data == "delete:yes")
async def cb_delete_yes(query: CallbackQuery):
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return

    chat = query.message.chat
    chat_id = chat.id
    thread_id = query.message.message_thread_id or 0

    conv = repo.get_or_create_conv(chat_id, thread_id, query.from_user.id)

    # 1. Удаляем из БД
    with db.conn() as c:
        c.execute("DELETE FROM messages WHERE conversation_id=?", (conv["id"],))
        c.execute("DELETE FROM conversations WHERE id=?", (conv["id"],))

    # 2. Если форум-топик — удаляем сам топик в Telegram
    deleted_topic = False
    topic_error = None
    if thread_id and chat.is_forum:
        try:
            await query.bot.delete_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
            deleted_topic = True
        except TelegramBadRequest as e:
            topic_error = str(e)
            log.warning("delete_forum_topic failed: %s", e)
        except Exception as e:
            topic_error = f"{type(e).__name__}: {e}"
            log.warning("delete_forum_topic unexpected error: %s", e)

    # 3. Подтверждение пользователю
    if deleted_topic:
        # Топик удалён — никакого answer не отправляем (некуда),
        # просто ack callback'а. Сообщение бота тоже исчезнет вместе с топиком.
        try:
            await query.answer("Беседа и топик удалены", show_alert=False)
        except Exception:
            pass
        return

    msg_parts = ["✅ Беседа удалена из БД."]
    if thread_id and chat.is_forum and topic_error:
        msg_parts.append(f"⚠️ Топик удалить не получилось: {topic_error[:200]}")
        msg_parts.append("Возможные причины: у бота нет права 'Управление темами', "
                         "или топик — главный (General).")
    elif chat.type == "private":
        msg_parts.append("ℹ️ Чтобы очистить переписку в ЛС — открой профиль бота → ⋮ → "
                         "«Очистить историю».")
    try:
        await query.message.edit_text("\n\n".join(msg_parts))
    except Exception:
        await query.answer("\n".join(msg_parts), show_alert=True)
    await query.answer()


@router.callback_query(F.data == "delete:no")
async def cb_delete_no(query: CallbackQuery):
    await query.message.edit_text("Отменено.")
    await query.answer()
