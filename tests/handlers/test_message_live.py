import asyncio
from logging import Logger
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Message

from handlers.message_live import LiveSessionPolicy, MessageLiveSession
from utils import rich


def policy(**overrides: object) -> LiveSessionPolicy:
    values = {
        "progress_enabled": True,
        "progress_min_interval": 1.0,
        "progress_max_interval": 15.0,
        "progress_backoff_factor": 2.0,
        "progress_reset_successes": 3,
        "progress_quiet_after": 600.0,
        "progress_quiet_interval": 30.0,
        "progress_chain_limit": 10,
        "progress_max_chars": 100,
        "progress_heartbeat_interval": 60.0,
        "progress_heartbeat_idle": 30.0,
        "typing_interval": 60.0,
        "draft_min_interval": 1.0,
    }
    values.update(overrides)
    return LiveSessionPolicy(**values)  # type: ignore[arg-type]


def make_session(
    *,
    rich_stream_enabled: bool = False,
    clock_value: float = 100.0,
) -> tuple[MessageLiveSession, MagicMock, MagicMock, MagicMock]:
    bot = MagicMock(spec=Bot)
    bot.send_chat_action = AsyncMock()
    source = MagicMock(spec=Message)
    source.chat = SimpleNamespace(id=10, type="private")
    source.message_thread_id = 20
    progress_message = MagicMock(spec=Message)
    source.answer = AsyncMock(return_value=progress_message)
    logger = MagicMock(spec=Logger)
    session = MessageLiveSession(
        bot=cast(Bot, bot),
        source_message=cast(Message, source),
        model="model",
        account_label="account",
        account_notes=None,
        attachments=[Path("brief.pdf")],
        started_at=90,
        rich_stream_enabled=rich_stream_enabled,
        policy=policy(),
        logger=cast(Logger, logger),
        clock=lambda: clock_value,
    )
    return session, bot, source, progress_message


@pytest.mark.asyncio
async def test_start_sends_initial_progress_and_typing_then_closes_tasks() -> None:
    session, bot, source, progress_message = make_session()

    await session.start()
    await asyncio.sleep(0)

    source.answer.assert_awaited_once()
    assert session.state.message is progress_message
    assert "brief.pdf" in source.answer.await_args.args[0]
    bot.send_chat_action.assert_awaited_with(10, "typing")

    await session.close()

    assert session._typing_task is not None and session._typing_task.done()
    assert session._heartbeat_task is not None and session._heartbeat_task.done()


@pytest.mark.asyncio
async def test_tool_event_forces_progress_and_updates_typed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _, _ = make_session()
    push = AsyncMock()
    monkeypatch.setattr(session.progress_delivery, "push", push)

    await session.progress_callback("partial", "tool_result", {"current_tool": "Read"})

    assert session.state.last_partial == "partial"
    assert session.state.last_meta == {"current_tool": "Read"}
    assert session.state.last_event_ts == 100
    push.assert_awaited_once_with(force=True)


@pytest.mark.asyncio
async def test_failed_rich_draft_disables_it_and_restores_partial_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, bot, _, _ = make_session(rich_stream_enabled=True)
    send_draft = AsyncMock(return_value=False)
    monkeypatch.setattr(rich, "send_draft", send_draft)
    monkeypatch.setattr(session.progress_delivery, "push", AsyncMock())

    await session.progress_callback("streamed", "assistant_delta", {})

    send_draft.assert_awaited_once_with(bot, 10, session.draft.draft_id, "streamed ▍", 20)
    assert not session.draft.enabled
    assert "streamed" in session._render()


@pytest.mark.asyncio
async def test_draft_throttle_skips_repeated_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _, _, _ = make_session(rich_stream_enabled=True)
    send_draft = AsyncMock(return_value=True)
    monkeypatch.setattr(rich, "send_draft", send_draft)
    monkeypatch.setattr(session.progress_delivery, "push", AsyncMock())

    await session.progress_callback("first", "assistant_delta", {})
    await session.progress_callback("second", "assistant_delta", {})

    send_draft.assert_awaited_once()
    assert session.state.last_partial == "second"


@pytest.mark.asyncio
async def test_overflow_ignores_later_provider_events(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _, _, _ = make_session()
    session.state.overflowed = True
    push = AsyncMock()
    monkeypatch.setattr(session.progress_delivery, "push", push)
    original_meta: dict[str, Any] = {"current_tool": "old"}
    session.state.last_meta = original_meta

    await session.progress_callback("new", "tool_use", {"current_tool": "new"})

    assert session.state.last_partial == ""
    assert session.state.last_meta is original_meta
    push.assert_not_awaited()
