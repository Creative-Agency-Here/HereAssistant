from logging import Logger
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Message

import handlers.message_rich_final as rich_final
from handlers.message_rich_final import (
    build_rich_markdown,
    deliver_rich_final,
    prepare_classic_tables,
)
from utils import rich


def test_build_rich_markdown_preserves_answer_and_limits_steps() -> None:
    markdown = build_rich_markdown(
        "# Ответ\n| a | b |",
        model="model-x",
        account_label="main",
        signature="\n\n— model-x · 1.0с",
        chain=["Read a", "Write b", "Test c"],
        steps_limit=2,
    )

    assert markdown.startswith("🤖 model-x · 👤 main\n\n# Ответ\n| a | b |")
    assert "**📋 Шаги (3)**\n1. Read a\n2. Write b\n…и ещё 1" in markdown
    assert markdown.endswith("---\n— model-x · 1.0с")


def test_classic_preparation_is_deferred_while_rich_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extractor = MagicMock(side_effect=AssertionError("must not render"))
    monkeypatch.setattr(rich_final, "replace_tables_with_placeholders", extractor)

    result = prepare_classic_tables(
        "| a |",
        rich_enabled=True,
        logger=cast(Logger, MagicMock(spec=Logger)),
    )

    assert result.answer == "| a |"
    assert result.table_pngs == ()
    extractor.assert_not_called()


def test_classic_preparation_extracts_tables_when_rich_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rich_final,
        "replace_tables_with_placeholders",
        MagicMock(return_value=("[table]", [b"png"])),
    )

    result = prepare_classic_tables(
        "| a |",
        rich_enabled=False,
        logger=cast(Logger, MagicMock(spec=Logger)),
    )

    assert result.answer == "[table]"
    assert result.table_pngs == (b"png",)


async def run_delivery(
    monkeypatch: pytest.MonkeyPatch,
    *,
    send_result: object,
    sane: bool = True,
    progress_message: Message | None = None,
):
    monkeypatch.setattr(rich, "sanity_check_markdown", MagicMock(return_value=sane))
    send = AsyncMock(return_value=send_result)
    monkeypatch.setattr(rich, "send_message", send)
    bot = cast(Bot, MagicMock(spec=Bot))
    logger = cast(Logger, MagicMock(spec=Logger))
    result = await deliver_rich_final(
        bot,
        chat_id=1,
        thread_id=2,
        answer="answer",
        model="model",
        account_label="account",
        signature="— signature",
        chain=["step"],
        steps_limit=10,
        progress_message=progress_message,
        rich_enabled=True,
        logger=logger,
    )
    return result, send, logger


@pytest.mark.asyncio
async def test_rich_success_deletes_progress_message(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = MagicMock(spec=Message)
    progress.delete = AsyncMock()

    result, send, _ = await run_delivery(
        monkeypatch,
        send_result={"message_id": 7},
        progress_message=cast(Message, progress),
    )

    assert result.done
    assert result.answer == "answer"
    await_args = send.await_args
    assert await_args is not None
    assert await_args.args[1] == 1
    assert await_args.args[3] == 2
    progress.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_rich_markdown_falls_back_to_table_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extractor = MagicMock(return_value=("classic", [b"png"]))
    monkeypatch.setattr(rich_final, "replace_tables_with_placeholders", extractor)

    result, send, _ = await run_delivery(monkeypatch, send_result=None, sane=False)

    assert not result.done
    assert result.answer == "classic"
    assert result.table_pngs == (b"png",)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_method_not_found_still_runs_classic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def disable_rich(*_args: object) -> None:
        rich._available = False

    monkeypatch.setattr(rich, "_available", True)
    monkeypatch.setattr(rich, "sanity_check_markdown", MagicMock(return_value=True))
    monkeypatch.setattr(rich, "send_message", disable_rich)
    extractor = MagicMock(return_value=("classic", [b"png"]))
    monkeypatch.setattr(rich_final, "replace_tables_with_placeholders", extractor)

    result = await deliver_rich_final(
        cast(Bot, MagicMock(spec=Bot)),
        chat_id=1,
        thread_id=0,
        answer="table",
        model=None,
        account_label=None,
        signature="— signature",
        chain=[],
        steps_limit=10,
        progress_message=None,
        rich_enabled=True,
        logger=cast(Logger, MagicMock(spec=Logger)),
    )

    assert not rich.enabled()
    assert result.answer == "classic"
    assert result.table_pngs == (b"png",)


def test_table_renderer_failure_preserves_text_and_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rich_final,
        "replace_tables_with_placeholders",
        MagicMock(side_effect=RuntimeError("renderer unavailable")),
    )
    logger = MagicMock(spec=Logger)

    result = prepare_classic_tables(
        "original",
        rich_enabled=False,
        logger=cast(Logger, logger),
    )

    assert result.answer == "original"
    assert result.table_pngs == ()
    logger.warning.assert_called_once()
