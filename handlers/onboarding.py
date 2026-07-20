"""Friendly first-run choices shared by /start, /help and account setup."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from core import access, config
from core.workspace_status import installation_identity, workspace_overview

from . import repo

router = Router()

COMMAND_CATALOG = """HereAssistant · все команды

Начать работу
/accounts — AI-аккаунты и переключение
/model — модель
/project — рабочий проект
/cwd — папка
/status — сессия, задачи, Git, диск и деплой
/web — открыть Web App

Сессия
/new — новый контекст
/reset — очистить историю
/delete — удалить беседу
/diff — правки последнего ответа

Git и запуск
/git — Git-аккаунты и репозитории
/version — версия
/deploy — мягкий перезапуск после завершения работы
/stats · /log · /rtk — статистика и диагностика

Команда
/users · /access — пользователи и доступ (админ)
/logout — снять свой доступ

Можно просто отправить задачу, документ, фото, аудио или видео."""


def welcome_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="💬 Продолжить в чате", callback_data="onb:chat"),
            InlineKeyboardButton(text="›_ Терминал", callback_data="onb:terminal"),
        ]
    ]
    if config.WEBAPP_URL:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🖥 Открыть Web App",
                    web_app=WebAppInfo(url=config.webapp_url()),
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(text="👤 AI-аккаунты", callback_data="onb:accounts"),
                InlineKeyboardButton(text="⌘ Все команды", callback_data="onb:commands"),
            ]
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_setup_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Как добавить аккаунт", callback_data="onb:accounts")]]
    if config.WEBAPP_URL:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔐 Git и подключения",
                    web_app=WebAppInfo(url=config.webapp_url("/settings")),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def welcome_text(user_id: int) -> str:
    accounts = repo.list_accounts(user_id)
    cwd = config.user_default_cwd(user_id)
    overview = workspace_overview(user_id, cwd)
    contour = installation_identity()
    crm = "подключена" if config.HERECRM_SYNC_URL and config.HERECRM_SYNC_TOKEN else "не подключена"
    return (
        "Привет! Я HereAssistant 👋\n"
        "Выбери режим ниже или сразу напиши задачу обычным сообщением.\n\n"
        f"Сейчас · {contour['label']}\n"
        f"🤖 AI-аккаунты: {len(accounts)}\n"
        f"🔐 Git: {overview['git']['connections']} подключений · "
        f"{overview['git']['repositories']} репозиториев доступно\n"
        f"📦 На диске агента: {overview['repositoriesOnDisk']} Git-репозиториев "
        f"из {overview['projectsOnDisk']} проектов\n"
        f"💾 Свободно: {overview['disk']['freeLabel']}\n"
        f"🔄 HereCRM: {crm}"
    )


@router.callback_query(F.data == "onb:chat")
async def choose_chat(query: CallbackQuery) -> None:
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return
    await query.message.answer(
        "Готов работать в чате. Напиши задачу одним сообщением — я покажу ход работы и итог."
    )
    await query.answer()


@router.callback_query(F.data == "onb:terminal")
async def choose_terminal(query: CallbackQuery) -> None:
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return
    await query.message.answer(
        "Терминальный режим\n\n"
        "На машине, где установлен HereAssistant:\n"
        "  .venv/bin/python chat.py\n\n"
        "Заголовок окна покажет текущую задачу, их количество и незавершённое состояние. "
        "Команда /status сверит HereCRM, Git, диск и подтверждение деплоя."
    )
    await query.answer()


@router.callback_query(F.data == "onb:accounts")
async def choose_accounts(query: CallbackQuery) -> None:
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return
    await query.message.answer(
        "Добавить AI-аккаунт безопасно можно на том контуре, где он будет работать:\n\n"
        "  .venv/bin/python manage.py\n"
        "  → «Добавить аккаунт»\n"
        "  → выбрать Claude, Codex или Gemini и пройти вход у провайдера\n\n"
        "Токены и пароли в Telegram отправлять не нужно. После входа вернись в /accounts."
    )
    await query.answer()


@router.callback_query(F.data == "onb:commands")
async def choose_commands(query: CallbackQuery) -> None:
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return
    await query.message.answer(COMMAND_CATALOG, reply_markup=welcome_keyboard())
    await query.answer()
