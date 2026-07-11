"""/accounts, /account [use|switch] с inline-кнопками."""

import logging

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (Message, CallbackQuery,
                            InlineKeyboardButton, InlineKeyboardMarkup)

from core import access, events
from . import repo
from .common import is_allowed

router = Router()
log = logging.getLogger("bridge.accounts")


def _accounts_keyboard(current_account_id: int | None) -> InlineKeyboardMarkup:
    buttons = []
    for acc in repo.list_accounts():
        mark = "✓ " if acc["id"] == current_account_id else "  "
        label = f"{mark}{acc['label']} ({acc['provider']})"
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"acc:use:{acc['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="✗ Отмена", callback_data="acc:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("accounts"))
async def cmd_accounts(message: Message):
    if not is_allowed(message):
        return
    accs = repo.list_accounts()
    if not accs:
        await message.answer(
            "Аккаунты не настроены. Запусти на сервере:\n"
            "  python manage.py\n"
            "и добавь через пункт 2."
        )
        return

    conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                    message.from_user.id)
    lines = ["Зарегистрированные аккаунты:"]
    for acc in accs:
        mark = "✓" if acc["id"] == conv["account_id"] else " "
        model = f" [{acc['default_model']}]" if acc["default_model"] else ""
        note = f" — {acc['notes']}" if acc["notes"] else ""
        lines.append(f"  {mark} {acc['label']} ({acc['provider']}){model}{note}")
    lines.append("\nНажми, чтобы переключиться:")
    await message.answer("\n".join(lines), reply_markup=_accounts_keyboard(conv["account_id"]))


@router.message(Command("account"))
async def cmd_account(message: Message, command: CommandObject):
    if not is_allowed(message):
        return
    args = (command.args or "").split()
    if not args:
        # без аргументов — показываем кнопки
        conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                        message.from_user.id)
        await message.answer("Выбери аккаунт:",
                             reply_markup=_accounts_keyboard(conv["account_id"]))
        return
    if args[0] != "use" or len(args) < 2:
        await message.answer("Использование: /account use <label>  или просто /account")
        return
    label = args[1]
    acc = repo.get_account_by_label(label)
    if not acc:
        await message.answer(f"Не нашёл активный аккаунт '{label}'.")
        return
    conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                    message.from_user.id)
    repo.update_conv(conv["id"], account_id=acc["id"],
                     model=acc["default_model"], provider_session_id=None)
    events.log("switch_account", user_id=message.from_user.id, chat_id=message.chat.id,
               thread_id=message.message_thread_id or 0,
               account_label=acc["label"], provider=acc["provider"], model=acc["default_model"])
    await message.answer(
        f"→ {acc['label']} ({acc['provider']}, model={acc['default_model']}). Сессия сброшена."
    )


@router.callback_query(F.data.startswith("acc:use:"))
async def cb_account_use(query: CallbackQuery):
    if not query.from_user or not access.is_allowed_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return
    acc_id = int(query.data.split(":")[-1])
    acc = repo.get_account(acc_id)
    if not acc:
        await query.answer("Аккаунт не найден")
        return
    conv = repo.get_or_create_conv(query.message.chat.id,
                                    query.message.message_thread_id or 0,
                                    query.from_user.id)
    repo.update_conv(conv["id"], account_id=acc["id"],
                     model=acc["default_model"], provider_session_id=None)
    events.log("switch_account", user_id=query.from_user.id, chat_id=query.message.chat.id,
               account_label=acc["label"], provider=acc["provider"], model=acc["default_model"])
    await query.message.edit_text(
        f"→ Переключился на {acc['label']} ({acc['provider']}, model={acc['default_model']})."
    )
    await query.answer()


@router.callback_query(F.data == "acc:cancel")
async def cb_account_cancel(query: CallbackQuery):
    await query.message.edit_text("Отменено.")
    await query.answer()

