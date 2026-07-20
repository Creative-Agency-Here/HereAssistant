"""Concise terminal title lifecycle with an interruptible working animation."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import TextIO

SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def compact_title(value: str, limit: int = 42) -> str:
    title = " ".join(value.split()).strip() or "Новая задача"
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def task_word(count: int) -> str:
    return (
        "задача"
        if count % 10 == 1 and count % 100 != 11
        else "задачи"
        if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14)
        else "задач"
    )


class TerminalTitle:
    def __init__(self, output: TextIO = sys.stdout, *, enabled: bool | None = None) -> None:
        self.output = output
        self.enabled = (
            bool(getattr(output, "isatty", lambda: False)()) and os.environ.get("TERM") != "dumb"
            if enabled is None
            else enabled
        )
        self._animation: asyncio.Task[None] | None = None
        self._prompt = ""
        self._count = 0

    def set(self, value: str) -> None:
        if not self.enabled:
            return
        safe = compact_title(value, 80).replace("\a", "").replace("\x1b", "")
        self.output.write(f"\033]0;{safe}\007")
        self.output.flush()

    def idle(self, cwd: str, open_tasks: int = 0) -> None:
        project = Path(cwd).name or "HereAssistant"
        mark = "✕" if open_tasks else "✓"
        self.set(f"{mark} Here · {open_tasks} {task_word(open_tasks)} · {project}")

    def start(self, prompt: str, task_count: int) -> None:
        self._prompt = compact_title(prompt)
        self._count = max(1, task_count)
        if self.enabled:
            self._animation = asyncio.create_task(self._animate())

    async def _animate(self) -> None:
        index = 0
        try:
            while True:
                self.set(
                    f"{SPINNER[index % len(SPINNER)]} {self._count} {task_word(self._count)} · {self._prompt}"
                )
                index += 1
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            return

    async def finish(self, *, completed: bool, cwd: str, open_tasks: int = 0) -> None:
        if self._animation is not None:
            self._animation.cancel()
            try:
                await self._animation
            except asyncio.CancelledError:
                pass
            self._animation = None
        if completed:
            self.idle(cwd, open_tasks)
        else:
            self.set(
                f"✕ {self._count or 1} {task_word(self._count or 1)} · {self._prompt or 'Сессия не завершена'}"
            )
