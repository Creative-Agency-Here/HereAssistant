import asyncio
from typing import Any

import pytest

from handlers.message_state import MessageRuntimeState, PendingMessage


@pytest.mark.asyncio
async def test_runtime_busy_state_covers_counter_tasks_and_pending() -> None:
    state = MessageRuntimeState()
    assert not state.is_busy()

    state.mark_started()
    assert state.is_busy()
    state.mark_finished()
    assert not state.is_busy()

    task = asyncio.create_task(asyncio.sleep(10))
    state.active_tasks[(100, 1, 0)] = task
    assert state.is_busy()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not state.is_busy()

    pending: Any = PendingMessage(texts=[], attachments=[], last_message=None, timer=None)
    state.pending[(100, 1, 0)] = pending
    assert state.is_busy()


def test_busy_counter_never_becomes_negative() -> None:
    state = MessageRuntimeState()

    state.mark_finished()
    state.mark_finished()

    assert state.busy_counter == 0
