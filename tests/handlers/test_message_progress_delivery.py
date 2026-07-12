from logging import Logger
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import Message

from handlers.message_progress import ProgressState
from handlers.message_progress_delivery import (
    ProgressDelivery,
    ProgressDeliveryPolicy,
)


def policy(**overrides: object) -> ProgressDeliveryPolicy:
    values = {
        "enabled": True,
        "started_at": 0.0,
        "base_interval": 2.0,
        "max_interval": 15.0,
        "backoff_factor": 2.0,
        "reset_successes": 3,
        "quiet_after": 600.0,
        "quiet_interval": 30.0,
    }
    values.update(overrides)
    return ProgressDeliveryPolicy(**values)  # type: ignore[arg-type]


def delivery(
    state: ProgressState,
    source: MagicMock,
    render: MagicMock,
    logger: MagicMock,
    *,
    now: float = 100.0,
) -> ProgressDelivery:
    return ProgressDelivery(
        state=state,
        source_message=cast(Message, source),
        render=render,
        policy=policy(),
        logger=cast(Logger, logger),
        clock=lambda: now,
    )


@pytest.mark.asyncio
async def test_first_push_creates_and_records_progress_message() -> None:
    state = ProgressState()
    source = MagicMock(spec=Message)
    sent = MagicMock(spec=Message)
    source.answer = AsyncMock(return_value=sent)
    render = MagicMock(return_value="progress")

    await delivery(state, source, render, MagicMock()).push()

    source.answer.assert_awaited_once_with("progress", parse_mode="HTML")
    assert state.message is sent
    assert state.last_displayed == "progress"
    assert state.last_edit_ts == 100
    assert state.success_streak == 1


@pytest.mark.asyncio
async def test_duplicate_render_is_not_sent_even_when_forced() -> None:
    state = ProgressState(last_displayed="same")
    source = MagicMock(spec=Message)
    source.answer = AsyncMock()
    render = MagicMock(return_value="same")

    await delivery(state, source, render, MagicMock()).push(force=True)

    source.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_after_sets_cooldown_and_backoff() -> None:
    state = ProgressState(min_interval=10, success_streak=2)
    source = MagicMock(spec=Message)
    source.answer = AsyncMock(
        side_effect=TelegramRetryAfter(
            method=SendMessage(chat_id=1, text="progress"),
            message="rate limited",
            retry_after=10,
        )
    )

    await delivery(state, source, MagicMock(return_value="progress"), MagicMock()).push()

    assert state.cooldown_until == 111
    assert state.min_interval == 15
    assert state.success_streak == 0


@pytest.mark.asyncio
async def test_not_modified_bad_request_is_expected_and_not_logged() -> None:
    state = ProgressState()
    source = MagicMock(spec=Message)
    source.answer = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=1, text="progress"),
            message="message is not modified",
        )
    )
    logger = MagicMock()

    await delivery(state, source, MagicMock(return_value="progress"), logger).push()

    logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_flood_error_is_classified_at_boundary() -> None:
    state = ProgressState(min_interval=2)
    source = MagicMock(spec=Message)
    source.answer = AsyncMock(side_effect=RuntimeError("retry after 17 seconds"))

    await delivery(state, source, MagicMock(return_value="progress"), MagicMock()).push()

    assert state.cooldown_until == 118
    assert state.min_interval == 4
