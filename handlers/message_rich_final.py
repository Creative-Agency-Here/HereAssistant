"""Rich-final orchestration с гарантированным classic table fallback."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import Message

from utils import rich
from utils.table_render import replace_tables_with_placeholders


@dataclass(frozen=True, slots=True)
class RichFinalResult:
    done: bool
    answer: str
    table_pngs: tuple[bytes, ...] = ()


def prepare_classic_tables(
    answer: str,
    *,
    rich_enabled: bool,
    logger: logging.Logger,
) -> RichFinalResult:
    """При выключенном rich заранее преобразует Markdown-таблицы в PNG."""
    if rich_enabled:
        return RichFinalResult(done=False, answer=answer)
    return _extract_tables(answer, logger=logger, log_context="table extraction failed")


async def deliver_rich_final(
    bot: Bot,
    *,
    chat_id: int,
    thread_id: int,
    answer: str,
    model: str | None,
    account_label: str | None,
    signature: str,
    chain: Sequence[object],
    steps_limit: int,
    progress_message: Message | None,
    rich_enabled: bool,
    logger: logging.Logger,
) -> RichFinalResult:
    if not rich_enabled:
        return RichFinalResult(done=False, answer=answer)

    markdown = build_rich_markdown(
        answer,
        model=model,
        account_label=account_label,
        signature=signature,
        chain=chain,
        steps_limit=steps_limit,
    )
    if rich.sanity_check_markdown(answer) and await rich.send_message(
        bot, chat_id, markdown, thread_id or None
    ):
        if progress_message is not None:
            try:
                await progress_message.delete()
            except Exception as error:
                # Rich final уже доставлен; cleanup progress — best effort.
                logger.debug("rich progress cleanup failed: %s", error)
        return RichFinalResult(done=True, answer=answer)

    # Используем snapshot rich_enabled. send_message может глобально выключить
    # rich после `method not found`, но classic fallback всё равно обязан сработать.
    return _extract_tables(
        answer,
        logger=logger,
        log_context="table extraction failed (fallback)",
    )


def build_rich_markdown(
    answer: str,
    *,
    model: str | None,
    account_label: str | None,
    signature: str,
    chain: Sequence[object],
    steps_limit: int,
) -> str:
    parts: list[str] = []
    header = " · ".join(
        part
        for part in (
            f"🤖 {model}" if model else "",
            f"👤 {account_label}" if account_label else "",
        )
        if part
    )
    if header:
        parts.append(header)
    parts.append(answer)
    if chain:
        shown = chain[:steps_limit]
        steps = "\n".join(f"{index}. {item}" for index, item in enumerate(shown, 1))
        remaining = len(chain) - len(shown)
        more = f"\n…и ещё {remaining}" if remaining else ""
        parts.append(f"**📋 Шаги ({len(chain)})**\n{steps}{more}")
    parts.append(f"---\n{signature.strip()}")
    return "\n\n".join(parts)


def _extract_tables(
    answer: str,
    *,
    logger: logging.Logger,
    log_context: str,
) -> RichFinalResult:
    try:
        prepared, table_pngs = replace_tables_with_placeholders(answer)
        return RichFinalResult(done=False, answer=prepared, table_pngs=tuple(table_pngs))
    except Exception as error:
        # Rendering is optional; preserve the textual answer on any renderer failure.
        logger.warning("%s: %s", log_context, error)
        return RichFinalResult(done=False, answer=answer)
