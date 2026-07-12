"""Общие проверки и хелперы для хендлеров."""

from aiogram.types import Message

from core import access
from utils.markdown import markdown_to_html, split_for_telegram


def is_allowed(message: Message) -> bool:
    """Допущен к работе с ботом: владелец (.env) либо approved в БД
    (с учётом режима доступа). Для обычных команд и сообщений агенту."""
    return bool(message.from_user) and access.is_allowed_id(message.from_user.id)


def is_admin(message: Message) -> bool:
    """Эффективный админ: владелец из .env ИЛИ role='admin' в БД.
    Для управляющих команд (/deploy, /users, /access, /logout других)."""
    return bool(message.from_user) and access.is_admin_id(message.from_user.id)


async def send_long(message: Message, text: str, html_already: bool = False):
    """Отправить с авторазбивкой. По умолчанию — конвертит markdown в HTML.

    html_already=True означает, что текст уже валидный Telegram HTML
    (например, собран из markdown_to_html + html_escape для подписи).
    """
    if not text:
        text = "(пусто)"
    payload = text if html_already else markdown_to_html(text)
    for chunk in split_for_telegram(payload):
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception:
            # если HTML битый (например, неудачно порезали посередине тега) —
            # отправляем как plain
            await message.answer(chunk)
