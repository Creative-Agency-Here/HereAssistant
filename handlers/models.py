"""/model — смена модели текущего аккаунта."""

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (Message, CallbackQuery,
                            InlineKeyboardButton, InlineKeyboardMarkup)

from core import events, config
from . import repo
from .common import is_admin

router = Router()

POPULAR_MODELS = {
    "claude_code": [
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "codex": ["gpt-5", "gpt-5-codex", "o3"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash"],
}


def _models_keyboard(provider: str, current: str | None) -> InlineKeyboardMarkup:
    buttons = []
    for m in POPULAR_MODELS.get(provider, []):
        mark = "✓ " if m == current else "  "
        buttons.append([InlineKeyboardButton(text=f"{mark}{m}", callback_data=f"mdl:set:{m}")])
    buttons.append([InlineKeyboardButton(text="✗ Отмена", callback_data="mdl:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("model"))
async def cmd_model(message: Message, command: CommandObject):
    if not is_admin(message):
        return
    conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                    message.from_user.id)
    if not conv["account_id"]:
        await message.answer("Сначала выбери аккаунт: /accounts")
        return
    acc = repo.get_account(conv["account_id"])
    if not command.args:
        # показать кнопки
        await message.answer(
            f"Текущий аккаунт: {acc['label']} ({acc['provider']})\n"
            f"Текущая модель: {conv['model'] or '—'}\n\nВыбери модель:",
            reply_markup=_models_keyboard(acc["provider"], conv["model"]),
        )
        return
    new_model = command.args.strip()
    repo.update_conv(conv["id"], model=new_model, provider_session_id=None)
    events.log("switch_model", user_id=message.from_user.id, chat_id=message.chat.id,
               account_label=acc["label"], provider=acc["provider"], model=new_model)
    await message.answer(f"Модель: {new_model}. Сессия сброшена.")


@router.callback_query(F.data.startswith("mdl:set:"))
async def cb_model_set(query: CallbackQuery):
    if not query.from_user or query.from_user.id != config.ADMIN_ID:
        await query.answer("Доступ запрещён", show_alert=True)
        return
    model = query.data.split(":", 2)[-1]
    conv = repo.get_or_create_conv(query.message.chat.id,
                                    query.message.message_thread_id or 0,
                                    query.from_user.id)
    if not conv["account_id"]:
        await query.answer("Сначала выбери аккаунт")
        return
    acc = repo.get_account(conv["account_id"])
    repo.update_conv(conv["id"], model=model, provider_session_id=None)
    events.log("switch_model", user_id=query.from_user.id,
               account_label=acc["label"], provider=acc["provider"], model=model)
    await query.message.edit_text(f"→ Модель: {model}. Сессия сброшена.")
    await query.answer()


@router.callback_query(F.data == "mdl:cancel")
async def cb_model_cancel(query: CallbackQuery):
    await query.message.edit_text("Отменено.")
    await query.answer()
