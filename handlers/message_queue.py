"""Очередь следующего turn-а одного thread.

Пока идёт запрос к CLI-агенту, новый ввод не должен запускать второй процесс в
том же проекте: два агента наперегонки правят одни файлы, а ссылка на первую
задачу теряется. Поэтому при отключённом прерывании (`INTERRUPT_ON_NEW_MESSAGE=0`)
ввод откладывается сюда и стартует после завершения текущего turn-а.

Модуль чистый: только склейка и хранение, без aiogram-логики и без запуска задач.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aiogram.types import Message

from .message_state import MessageRuntimeState, ThreadKey


@dataclass(slots=True)
class QueuedRun:
    """Отложенный запрос: всё, что нужно для запуска turn-а."""

    conv: object
    text: str
    message: Message
    main_attachment: Path | None = None
    attachments: list[Path] = field(default_factory=list)


def merge_runs(existing: QueuedRun | None, incoming: QueuedRun) -> QueuedRun:
    """Склеивает отложенный запрос с новым.

    Несколько сообщений, пришедших за время работы агента, дают один следующий
    turn, а не очередь из N запусков. Отвечаем всегда в последнее сообщение,
    контекст диалога тоже берём свежий.
    """
    if existing is None:
        return incoming

    parts = [part for part in (existing.text, incoming.text) if part]
    attachments = list(existing.attachments)
    for item in incoming.attachments:
        if item not in attachments:
            attachments.append(item)

    return QueuedRun(
        conv=incoming.conv,
        text="\n\n".join(parts),
        message=incoming.message,
        main_attachment=existing.main_attachment or incoming.main_attachment,
        attachments=attachments,
    )


def queue_run(state: MessageRuntimeState, *, key: ThreadKey, run: QueuedRun) -> QueuedRun:
    """Кладёт запрос в очередь ключа, склеивая с уже отложенным."""
    merged = merge_runs(state.queued.get(key), run)
    state.queued[key] = merged
    return merged


def pop_run(state: MessageRuntimeState, key: ThreadKey) -> QueuedRun | None:
    """Забирает отложенный запрос ключа, если он есть."""
    run = state.queued.pop(key, None)
    if run is None:
        return None
    if not isinstance(run, QueuedRun):  # pragma: no cover — защита от чужой записи в state
        return None
    return run
