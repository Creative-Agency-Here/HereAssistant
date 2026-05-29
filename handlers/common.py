"""Общие проверки и хелперы для хендлеров."""

from aiogram import Bot
from aiogram.types import Message

from core import config
from utils.markdown import markdown_to_html, split_for_telegram


def is_admin(message: Message) -> bool:
    return (config.ADMIN_ID is not None
            and message.from_user
            and message.from_user.id == config.ADMIN_ID)


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
