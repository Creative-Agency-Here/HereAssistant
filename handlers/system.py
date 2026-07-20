"""Системные команды: /status, /version, /help, /new, /reset, /delete."""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from core import access, config, crm_sync, db, git_connections, rtk, version
from core.workspace_status import workspace_overview

from . import repo
from .common import is_allowed
from .onboarding import COMMAND_CATALOG, welcome_keyboard

log = logging.getLogger(__name__)

router = Router()

_GIT_PROVIDER_LABELS = {
    "gitea": "Gitea",
    "github": "GitHub",
    "gitlab": "GitLab",
}
_GIT_STATUS_LABELS = {
    "active": "подключён",
    "pending": "ожидает",
    "expired": "нужно обновить доступ",
    "revoked": "отключён",
    "error": "ошибка",
}


def _safe_git_label(value: object, fallback: str = "—") -> str:
    """Однострочное безопасное представление внешнего Git metadata для Telegram."""
    label = " ".join(str(value or "").split()).strip()
    return label[:80] or fallback


def _git_connection_line(connection: object) -> str:
    provider = _safe_git_label(connection["provider"], "Git")  # type: ignore[index]
    provider = _GIT_PROVIDER_LABELS.get(provider.lower(), provider)
    host = _safe_git_label(connection["host"])  # type: ignore[index]
    login = _safe_git_label(connection["external_login"])  # type: ignore[index]
    status = _safe_git_label(connection["status"])  # type: ignore[index]
    status = _GIT_STATUS_LABELS.get(status, status)
    return f"• {provider} · {host} · {login} — {status}"


@router.message(Command("web"))
async def cmd_web(message: Message):
    """Кнопка-вход в веб-интерфейс ассистента (Telegram Mini App)."""
    if not is_allowed(message):
        return
    if not config.WEBAPP_URL:
        await message.answer("WEBAPP_URL не задан в .env — открыть нечего.")
        return
    web_url = config.webapp_url()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖥 Открыть ассистента", web_app=WebAppInfo(url=web_url))]
        ]
    )
    await message.answer(
        "Веб-интерфейс ассистента: текущая задача, история диалогов, прогресс в реальном времени.",
        reply_markup=kb,
    )


@router.message(Command("git"))
async def cmd_git(message: Message):
    """Безопасная точка входа в личные Git accounts без credentials в чате."""
    if not is_allowed(message) or not message.from_user:
        return
    if not config.WEBAPP_URL:
        await message.answer("WEBAPP_URL не задан — Git-настройки пока недоступны.")
        return
    connections = git_connections.list_connections(message.from_user.id)
    active = sum(row["status"] == "active" for row in connections)
    expired = sum(row["status"] == "expired" for row in connections)
    connection_lines = "\n".join(_git_connection_line(row) for row in connections[:10])
    if len(connections) > 10:
        connection_lines += f"\n…и ещё {len(connections) - 10}"
    web_url = config.webapp_url("/settings")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔐 Открыть мои Git-аккаунты",
                    web_app=WebAppInfo(url=web_url),
                )
            ]
        ]
    )
    await message.answer(
        "Git-аккаунты\n"
        f"Подключено: {active}"
        + (f" · требуют обновления: {expired}" if expired else "")
        + (f"\n\n{connection_lines}" if connection_lines else "")
        + "\n\nЛогин и пароль вводятся только на стороне Git provider-а — не отправляйте PAT в чат.",
        reply_markup=keyboard,
    )


@router.message(Command("help"))
async def cmd_help(message: Message, command: CommandObject):
    if not is_allowed(message):
        return
    await message.answer(COMMAND_CATALOG, reply_markup=welcome_keyboard())


@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_allowed(message):
        return
    conv = repo.get_or_create_conv(
        message.chat.id, message.message_thread_id or 0, message.from_user.id
    )
    acc = repo.get_account(conv["account_id"], message.from_user.id) if conv["account_id"] else None
    with db.conn() as c:
        msg_count = c.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (conv["id"],)
        ).fetchone()["n"]
    v = version.bot_version()
    sync = crm_sync.status()
    overview = workspace_overview(message.from_user.id, conv["cwd"])
    current_git = overview["git"]["current"]
    git_line = "не Git-проект"
    if current_git.get("available"):
        git_line = (
            f"{current_git['branch']} · изменений {current_git['dirty']} · "
            f"отправить {current_git['ahead']} · получить {current_git['behind']}"
        )
    deploy_labels = {
        "deployed": "задеплоено",
        "partial": "частично",
        "pending": "ожидает",
        "unknown": "нет подтверждения",
    }
    lines = [
        "HereAssistant · текущее состояние",
        f"🤖 {acc['label'] if acc else 'аккаунт не выбран'} · {conv['model'] or 'модель не выбрана'}",
        f"📁 {conv['project_name'] or conv['cwd']}",
        f"💬 Сессия: {(conv['provider_session_id'] or 'новая')[:12]} · {msg_count} сообщений",
        f"📋 Задачи HereCRM: {overview['tasks']['open']} в работе",
        f"🔀 Git: {git_line}",
        f"🚀 Деплой: {deploy_labels.get(overview['deployment']['state'], 'нет подтверждения')}",
        f"📦 Репозитории: {overview['git']['repositories']} доступно · {overview['repositoriesOnDisk']} на диске",
        f"💾 Свободно: {overview['disk']['freeLabel']}",
        (
            f"🔄 HereCRM: {'подключена' if sync['configured'] else 'выключена'}"
            f" · очередь {sync['pending']} · {sync['origin']}"
        ),
        f"🏷 Версия: {v['short']} ({v['mtime']})",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("version"))
async def cmd_version(message: Message):
    if not is_allowed(message):
        return
    v = version.bot_version()
    await message.answer(
        f"bot.py\nhash: {v['short']} (полный: {v['hash'][:16]}...)\nmtime: {v['mtime']}"
    )


@router.message(Command("rtk"))
async def cmd_rtk(message: Message):
    if not is_allowed(message):
        return
    savings = rtk.user_savings(message.from_user.id)
    if not savings["available"]:
        await message.answer("RTK не установлен на сервере.")
        return
    if not savings["accounts"]:
        await message.answer("Нет личных provider-аккаунтов с RTK-статистикой.")
        return
    await message.answer(
        "RTK · экономия контекста\n"
        f"Команд обработано: {savings['commands']}\n"
        f"Токенов до/после: {savings['input_tokens']} → {savings['output_tokens']}\n"
        f"Сэкономлено: {savings['saved_tokens']} ({savings['savings_pct']}%)\n"
        f"Сегодня: {savings['today_commands']} команд · −{savings['today_saved_tokens']} токенов\n\n"
        "Учитывается вывод поддерживаемых shell-команд; текст запроса и ответ модели не входят."
    )


@router.message(Command("new"))
async def cmd_new(message: Message):
    if not is_allowed(message):
        return
    conv = repo.get_or_create_conv(
        message.chat.id, message.message_thread_id or 0, message.from_user.id
    )
    repo.update_conv(conv["id"], provider_session_id=None)
    await message.answer("Новая сессия (session_id сброшен, история сохранена).")


def _reset_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, очистить", callback_data="reset:yes")],
            [InlineKeyboardButton(text="✗ Отмена", callback_data="reset:no")],
        ]
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message):
    if not is_allowed(message):
        return
    await message.answer(
        "Очистить историю этого чата и сбросить сессию?", reply_markup=_reset_keyboard()
    )


@router.callback_query(F.data == "reset:yes")
async def cb_reset_yes(query: CallbackQuery):
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return
    conv = repo.get_or_create_conv(
        query.message.chat.id, query.message.message_thread_id or 0, query.from_user.id
    )
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить беседу", callback_data="delete:yes")],
            [InlineKeyboardButton(text="✗ Отмена", callback_data="delete:no")],
        ]
    )


@router.message(Command("delete"))
async def cmd_delete(message: Message):
    if not is_allowed(message):
        return
    chat_type = message.chat.type
    thread_id = message.message_thread_id or 0
    if thread_id and message.chat.is_forum:
        warn = "🗑 Беседа будет удалена из БД, а топик — удалён из Telegram."
    elif chat_type == "private":
        warn = (
            "🗑 Беседа будет удалена из БД.\n"
            "⚠️ В ЛС Telegram не позволяет боту удалить чат целиком — "
            "переписку придётся очистить вручную (Очистить историю)."
        )
    else:
        warn = (
            "🗑 Беседа будет удалена из БД.\n"
            "⚠️ Топик/чат в Telegram удалить нельзя (это не форум-топик)."
        )
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
        msg_parts.append(
            "Возможные причины: у бота нет права 'Управление темами', "
            "или топик — главный (General)."
        )
    elif chat.type == "private":
        msg_parts.append(
            "ℹ️ Чтобы очистить переписку в ЛС — открой профиль бота → ⋮ → «Очистить историю»."
        )
    try:
        await query.message.edit_text("\n\n".join(msg_parts))
    except Exception:
        await query.answer("\n".join(msg_parts), show_alert=True)
    await query.answer()


@router.callback_query(F.data == "delete:no")
async def cb_delete_no(query: CallbackQuery):
    await query.message.edit_text("Отменено.")
    await query.answer()
