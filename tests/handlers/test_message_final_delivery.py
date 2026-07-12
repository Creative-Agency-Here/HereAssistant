from logging import Logger
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup, Message

import handlers.message_final_delivery as delivery_module
from handlers.message_final import FinalAttachment
from handlers.message_final_delivery import FinalDelivery, FinalDeliveryRequest
from handlers.message_progress import ProgressState


def request(**overrides: object) -> FinalDeliveryRequest:
    values = {
        "html": "<b>final</b>",
        "rich_done": False,
        "edits_markup": None,
        "attachments": (),
        "table_pngs": (),
        "chat_id": 10,
        "thread_id": 20,
    }
    values.update(overrides)
    return FinalDeliveryRequest(**values)  # type: ignore[arg-type]


def setup_delivery(
    state: ProgressState,
    *,
    sleep: AsyncMock | None = None,
) -> tuple[FinalDelivery, MagicMock, MagicMock, MagicMock]:
    bot = MagicMock(spec=Bot)
    bot.send_document = AsyncMock()
    bot.send_photo = AsyncMock()
    source = MagicMock(spec=Message)
    source.answer = AsyncMock()
    logger = MagicMock(spec=Logger)
    instance = FinalDelivery(
        bot=cast(Bot, bot),
        source_message=cast(Message, source),
        progress=state,
        logger=cast(Logger, logger),
        sleep=sleep or AsyncMock(),
    )
    return instance, bot, source, logger


@pytest.mark.asyncio
async def test_replaces_progress_and_attaches_button() -> None:
    progress_message = MagicMock(spec=Message)
    progress_message.edit_text = AsyncMock()
    state = ProgressState(message=cast(Message, progress_message))
    instance, _, source, _ = setup_delivery(state)
    markup = cast(InlineKeyboardMarkup, MagicMock())

    await instance.deliver(request(edits_markup=markup))

    progress_message.edit_text.assert_awaited_once_with(
        "<b>final</b>", parse_mode="HTML", reply_markup=markup
    )
    source.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_edit_falls_back_to_send_and_separate_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress_message = MagicMock(spec=Message)
    progress_message.edit_text = AsyncMock(side_effect=RuntimeError("edit failed"))
    state = ProgressState(message=cast(Message, progress_message))
    instance, _, source, _ = setup_delivery(state)
    send_long = AsyncMock()
    monkeypatch.setattr(delivery_module, "send_long", send_long)
    markup = cast(InlineKeyboardMarkup, MagicMock())

    await instance.deliver(request(edits_markup=markup))

    send_long.assert_awaited_once_with(source, "<b>final</b>", html_already=True)
    source.answer.assert_awaited_once_with(
        "📋 Правки этого запроса — открыть в вебапе:", reply_markup=markup
    )


@pytest.mark.asyncio
async def test_retry_after_waits_once_and_retries_edit() -> None:
    retry = TelegramRetryAfter(
        method=SendMessage(chat_id=1, text="final"),
        message="limited",
        retry_after=4,
    )
    progress_message = MagicMock(spec=Message)
    progress_message.edit_text = AsyncMock(side_effect=[retry, None])
    state = ProgressState(message=cast(Message, progress_message))
    sleep = AsyncMock()
    instance, _, _, _ = setup_delivery(state, sleep=sleep)

    await instance.deliver(request())

    sleep.assert_awaited_once_with(5.0)
    assert progress_message.edit_text.await_count == 2


@pytest.mark.asyncio
async def test_overflow_keeps_progress_tail_and_sends_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress_message = MagicMock(spec=Message)
    progress_message.edit_text = AsyncMock()
    state = ProgressState(
        message=cast(Message, progress_message),
        overflowed=True,
        last_displayed="header\n\nvisible tail",
    )
    instance, _, source, _ = setup_delivery(state)
    send_long = AsyncMock()
    monkeypatch.setattr(delivery_module, "send_long", send_long)

    await instance.deliver(request())

    progress_message.edit_text.assert_awaited_once_with("visible tail", parse_mode="HTML")
    send_long.assert_awaited_once_with(source, "<b>final</b>", html_already=True)


@pytest.mark.asyncio
async def test_sends_files_and_table_images_after_rich_final() -> None:
    instance, bot, _, _ = setup_delivery(ProgressState())

    await instance.deliver(
        request(
            rich_done=True,
            attachments=(FinalAttachment(b"document", "answer.md"),),
            table_pngs=(b"png",),
        )
    )

    document = bot.send_document.await_args.kwargs["document"]
    photo = bot.send_photo.await_args.kwargs["photo"]
    assert document.filename == "answer.md"
    assert photo.filename == "table_1.png"
    assert bot.send_document.await_args.kwargs["message_thread_id"] == 20
