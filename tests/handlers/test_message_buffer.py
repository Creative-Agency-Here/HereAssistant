import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from handlers.message_buffer import enqueue_message
from handlers.message_state import MessageRuntimeState


@pytest.mark.asyncio
async def test_enqueue_aggregates_parts_and_replaces_timer() -> None:
    state = MessageRuntimeState()
    first_message = cast(Message, MagicMock(spec=Message))
    second_message = cast(Message, MagicMock(spec=Message))
    flush = AsyncMock()

    enqueue_message(
        state,
        key=(100, 1, 2),
        text="first",
        attachment=Path("one.txt"),
        message=first_message,
        delay=60,
        flush=flush,
    )
    first_timer = state.pending[(100, 1, 2)]["timer"]
    enqueue_message(
        state,
        key=(100, 1, 2),
        text="second",
        attachment=Path("two.txt"),
        message=second_message,
        delay=60,
        flush=flush,
    )

    pending = state.pending[(100, 1, 2)]
    assert pending["texts"] == ["first", "second"]
    assert pending["attachments"] == [Path("one.txt"), Path("two.txt")]
    assert pending["last_message"] is second_message
    assert first_timer is not None
    assert pending["timer"] is not first_timer

    second_timer = pending["timer"]
    assert second_timer is not None
    second_timer.cancel()
    await asyncio.sleep(0)
    flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_timer_flushes_the_thread_key_after_delay() -> None:
    state = MessageRuntimeState()
    flush = AsyncMock()

    enqueue_message(
        state,
        key=(300, 10, 20),
        text="hello",
        attachment=None,
        message=cast(Message, MagicMock(spec=Message)),
        delay=0,
        flush=flush,
    )
    timer = state.pending[(300, 10, 20)]["timer"]
    assert timer is not None

    await timer

    flush.assert_awaited_once_with((300, 10, 20))
