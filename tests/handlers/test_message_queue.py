import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from handlers import messages as messages_module
from handlers.message_queue import QueuedRun, merge_runs, pop_run, queue_run
from handlers.message_state import MessageRuntimeState


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    """Ждёт условия, прокручивая event loop: эстафета идёт через done-callback."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("условие не наступило за отведённое время")
        await asyncio.sleep(0.005)


def _run(text: str, *, attachments: list[Path] | None = None) -> QueuedRun:
    return QueuedRun(
        conv={"id": 1},
        text=text,
        message=cast(Message, MagicMock(spec=Message)),
        attachments=attachments or [],
    )


def test_merge_returns_incoming_when_queue_is_empty() -> None:
    incoming = _run("первое")

    assert merge_runs(None, incoming) is incoming


def test_merge_glues_texts_and_keeps_last_message() -> None:
    first = _run("первое")
    second = _run("второе")

    merged = merge_runs(first, second)

    assert merged.text == "первое\n\nвторое"
    assert merged.message is second.message
    assert merged.conv is second.conv


def test_merge_skips_empty_text_parts() -> None:
    merged = merge_runs(_run(""), _run("только текст"))

    assert merged.text == "только текст"


def test_merge_accumulates_attachments_without_duplicates() -> None:
    shared = Path("/tmp/a.png")
    merged = merge_runs(
        _run("первое", attachments=[shared]),
        _run("второе", attachments=[shared, Path("/tmp/b.png")]),
    )

    assert merged.attachments == [shared, Path("/tmp/b.png")]


def test_merge_keeps_earliest_main_attachment() -> None:
    first = _run("первое")
    first.main_attachment = Path("/tmp/first.png")
    second = _run("второе")
    second.main_attachment = Path("/tmp/second.png")

    assert merge_runs(first, second).main_attachment == Path("/tmp/first.png")


def test_queue_and_pop_roundtrip() -> None:
    state = MessageRuntimeState()
    key = (1, 2, 0)

    queue_run(state, key=key, run=_run("первое"))
    queue_run(state, key=key, run=_run("второе"))

    assert state.is_busy(), "отложенный запрос считается занятостью"
    queued = pop_run(state, key)

    assert queued is not None
    assert queued.text == "первое\n\nвторое"
    assert pop_run(state, key) is None
    assert not state.is_busy()


@pytest.mark.asyncio
async def test_queued_turn_starts_after_current_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отложенный запрос стартует сам, когда текущий turn закончился."""
    state = MessageRuntimeState()
    monkeypatch.setattr(messages_module, "runtime", state)

    started: list[str] = []
    release = asyncio.Event()

    async def fake_process(_bot, _message, _conv, text, _attachment, all_attachments=None):
        started.append(text)
        await release.wait()

    monkeypatch.setattr(messages_module, "_process_message", fake_process)

    bot = MagicMock()
    key = (1, 2, 0)

    messages_module._start_run(bot, key, _run("первый"))
    await asyncio.sleep(0)
    queue_run(state, key=key, run=_run("второй"))

    assert started == ["первый"], "второй turn не должен идти параллельно"

    release.set()
    await _wait_until(lambda: len(started) == 2)

    assert started == ["первый", "второй"]
    assert pop_run(state, key) is None


@pytest.mark.asyncio
async def test_flush_queues_instead_of_starting_second_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При выключенном прерывании второй CLI не запускается параллельно."""
    state = MessageRuntimeState()
    monkeypatch.setattr(messages_module, "runtime", state)
    monkeypatch.setattr(messages_module, "INTERRUPT_ON_NEW", False)

    started: list[str] = []

    async def fake_process(_bot, _message, _conv, text, _attachment, all_attachments=None):
        started.append(text)

    monkeypatch.setattr(messages_module, "_process_message", fake_process)
    monkeypatch.setattr(
        messages_module.repo, "get_or_create_conv", lambda *_args: {"account_id": 7}
    )

    async def fake_prepare(_message, texts, attachments, logger=None):
        prepared = MagicMock()
        prepared.text = " ".join(texts)
        prepared.main_attachment = None
        prepared.attachments = attachments
        return prepared

    monkeypatch.setattr(messages_module, "prepare_message_input", fake_prepare)

    key = (1, 2, 0)
    message = cast(Message, MagicMock(spec=Message))
    message.answer = AsyncMock()

    busy = asyncio.create_task(asyncio.Event().wait())
    state.active_tasks[key] = cast(asyncio.Task, busy)
    state.pending[key] = {
        "texts": ["новое сообщение"],
        "attachments": [],
        "last_message": message,
        "timer": None,
    }

    await messages_module._flush_pending(MagicMock(), key)

    assert started == [], "во время активной задачи второй turn стартовать не должен"
    queued = pop_run(state, key)
    assert queued is not None and queued.text == "новое сообщение"
    assert state.active_tasks[key] is busy, "активная задача не должна подменяться"

    busy.cancel()


@pytest.mark.asyncio
async def test_cancelled_turn_drops_queued_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """После /stop или выключения бота отложенный запрос не стартует сам."""
    state = MessageRuntimeState()
    monkeypatch.setattr(messages_module, "runtime", state)

    started: list[str] = []

    async def fake_process(_bot, _message, _conv, text, _attachment, all_attachments=None):
        started.append(text)
        await asyncio.Event().wait()

    monkeypatch.setattr(messages_module, "_process_message", fake_process)

    key = (1, 2, 0)
    task = messages_module._start_run(MagicMock(), key, _run("первый"))
    await asyncio.sleep(0)
    queue_run(state, key=key, run=_run("отложенный"))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.02)

    assert started == ["первый"]
    assert pop_run(state, key) is None, "очередь очищается, а не копится"


@pytest.mark.asyncio
async def test_stale_task_does_not_start_queued_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Эстафету передаёт только актуальная задача ключа, а не вытесненная."""
    state = MessageRuntimeState()
    monkeypatch.setattr(messages_module, "runtime", state)

    started: list[str] = []

    async def fake_process(_bot, _message, _conv, text, _attachment, all_attachments=None):
        started.append(text)

    monkeypatch.setattr(messages_module, "_process_message", fake_process)

    key = (1, 2, 0)
    stale = asyncio.create_task(asyncio.sleep(0))
    await stale
    queue_run(state, key=key, run=_run("отложенный"))

    messages_module._run_next_queued(MagicMock(), key, stale)
    await asyncio.sleep(0)

    assert started == []
    assert pop_run(state, key) is not None
