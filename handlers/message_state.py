"""Process-local state входящих сообщений и активных задач."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from aiogram.types import Message

# user_id обязателен: в group/topic разные пользователи не должны делить debounce/task.
ThreadKey = tuple[int, int, int]


class PendingMessage(TypedDict):
    texts: list[str]
    attachments: list[Path]
    last_message: Message | None
    timer: asyncio.Task[None] | None


@dataclass(slots=True)
class MessageRuntimeState:
    active_tasks: dict[ThreadKey, asyncio.Task[None]] = field(default_factory=dict)
    pending: dict[ThreadKey, PendingMessage] = field(default_factory=dict)
    busy_counter: int = 0

    def is_busy(self) -> bool:
        return (
            self.busy_counter > 0
            or any(not task.done() for task in self.active_tasks.values())
            or bool(self.pending)
        )

    def mark_started(self) -> None:
        self.busy_counter += 1

    def mark_finished(self) -> None:
        self.busy_counter = max(0, self.busy_counter - 1)


runtime = MessageRuntimeState()
