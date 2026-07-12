"""Debounce-буфер входящих Telegram-сообщений одного thread."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiogram.types import Message

from .message_state import MessageRuntimeState, PendingMessage, ThreadKey

FlushCallback = Callable[[ThreadKey], Awaitable[None]]


def enqueue_message(
    state: MessageRuntimeState,
    *,
    key: ThreadKey,
    text: str,
    attachment: Path | None,
    message: Message,
    delay: float,
    flush: FlushCallback,
) -> None:
    pending = state.pending.get(key)
    if pending is None:
        pending = PendingMessage(texts=[], attachments=[], last_message=None, timer=None)
        state.pending[key] = pending
    else:
        timer = pending["timer"]
        if timer is not None and not timer.done():
            timer.cancel()

    if text:
        pending["texts"].append(text)
    if attachment is not None:
        pending["attachments"].append(attachment)
    pending["last_message"] = message

    async def delayed_flush() -> None:
        try:
            await asyncio.sleep(delay)
            await flush(key)
        except asyncio.CancelledError:
            pass

    pending["timer"] = asyncio.create_task(delayed_flush())
