"""Telegram boundary финального ответа и дополнительных файлов."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from .common import send_long
from .message_final import FinalAttachment
from .message_progress import ProgressState


@dataclass(frozen=True, slots=True)
class FinalDeliveryRequest:
    html: str
    rich_done: bool
    edits_markup: InlineKeyboardMarkup | None
    attachments: Sequence[FinalAttachment]
    table_pngs: Sequence[bytes]
    chat_id: int
    thread_id: int


class FinalDelivery:
    def __init__(
        self,
        *,
        bot: Bot,
        source_message: Message,
        progress: ProgressState,
        logger: logging.Logger,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.source_message = source_message
        self.progress = progress
        self.logger = logger
        self.sleep = sleep

    async def deliver(self, request: FinalDeliveryRequest) -> None:
        button_attached = False
        if not request.rich_done:
            button_attached = await self._deliver_text(request)

        if request.edits_markup is not None and not button_attached:
            try:
                await self.source_message.answer(
                    "📋 Правки этого запроса — открыть в вебапе:",
                    reply_markup=request.edits_markup,
                )
            except Exception as error:
                # Неуспех вспомогательной кнопки не должен отменять уже отправленный ответ.
                self.logger.warning("edits button send failed: %s", error)

        await self._send_attachments(request)

    async def _deliver_text(self, request: FinalDeliveryRequest) -> bool:
        progress_message = self.progress.message
        can_replace = (
            progress_message is not None
            and not self.progress.overflowed
            and len(request.html) <= 4000
        )
        if can_replace:
            assert progress_message is not None

            async def edit() -> None:
                await progress_message.edit_text(
                    request.html or "(пусто)",
                    parse_mode="HTML",
                    reply_markup=request.edits_markup,
                )

            if await self._with_retry(edit):
                return request.edits_markup is not None
            await self._with_retry(lambda: self._send_long(request.html))
            return False

        if progress_message is not None:
            try:
                tail = self.progress.last_displayed.split("\n\n", 1)[-1][:4000] or "(прогресс)"
                await progress_message.edit_text(tail, parse_mode="HTML")
            except Exception as error:
                # Best-effort cosmetic cleanup; final answer is still sent below.
                self.logger.debug("progress tail cleanup failed: %s", error)
        await self._with_retry(lambda: self._send_long(request.html))
        return False

    async def _send_long(self, html: str) -> None:
        await send_long(self.source_message, html, html_already=True)

    async def _with_retry(self, operation: Callable[[], Awaitable[None]]) -> bool:
        try:
            await operation()
            return True
        except TelegramRetryAfter as error:
            wait = float(error.retry_after or 10) + 1.0
            self.logger.warning("final: FloodWait, sleep %.0fs and retry", wait)
            await self.sleep(wait)
            try:
                await operation()
                return True
            except Exception as retry_error:
                self.logger.warning("final retry failed: %s", retry_error)
                return False
        except TelegramBadRequest as error:
            return "not modified" in str(error).lower()
        except Exception as error:
            # Boundary catch: final delivery degrades from edit to send instead of
            # failing the completed provider request.
            self.logger.warning("final send failed: %s", error)
            return False

    async def _send_attachments(self, request: FinalDeliveryRequest) -> None:
        thread_id = request.thread_id or None
        for attachment in request.attachments:
            try:
                await self.bot.send_document(
                    chat_id=request.chat_id,
                    message_thread_id=thread_id,
                    document=BufferedInputFile(attachment.data, filename=attachment.filename),
                )
            except Exception as error:
                self.logger.warning("send attachment %s failed: %s", attachment.filename, error)

        for index, png in enumerate(request.table_pngs, 1):
            try:
                await self.bot.send_photo(
                    chat_id=request.chat_id,
                    message_thread_id=thread_id,
                    photo=BufferedInputFile(png, filename=f"table_{index}.png"),
                )
            except Exception as error:
                self.logger.warning("send table png #%d failed: %s", index, error)
