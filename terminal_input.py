"""Multiline terminal input with click-to-position and soft wrapping."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings


def _mouse_default() -> bool:
    """Мышь включена по умолчанию (как в Claude Code / Codex).
    Выделение текста — через Shift+drag (стандарт терминалов с mouse reporting).
    Выключить: HA_MOUSE=0."""
    v = os.environ.get("HA_MOUSE", "").strip()
    if v in ("0", "false", "no"):
        return False
    return True


class SlashCommandCompleter(Completer):
    """Показывает каталог команд только в начале slash-запроса."""

    def __init__(self, commands: tuple[tuple[str, str], ...]) -> None:
        self._commands = commands

    def get_completions(self, document: Document, complete_event):  # type: ignore[no-untyped-def]
        before = document.text_before_cursor
        if not before.startswith("/") or any(char.isspace() for char in before):
            return
        needle = before.lower()
        for command, description in self._commands:
            if command.startswith(needle):
                yield Completion(
                    command,
                    start_position=-len(before),
                    display=command,
                    display_meta=description,
                )


class TerminalPrompt:
    """Claude-like editor: Enter sends, Alt+Enter adds a line, paste stays multiline.

    Мышь по умолчанию выключена (HA_MOUSE=1 для включения): mouse reporting
    перехватывает выделение и ломает копирование soft-wrap строк.
    Переключение на лету — /mouse в чате.
    """

    def __init__(
        self,
        *,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        fallback: Callable[[str], str] | None = None,
        commands: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._fallback = fallback
        self._interactive = bool(input_stream.isatty() and output_stream.isatty())
        self._mouse = _mouse_default()
        self._session: PromptSession[str] | None = None
        if self._interactive:
            bindings = KeyBindings()

            @bindings.add("enter")
            def submit(event) -> None:  # type: ignore[no-untyped-def]
                event.current_buffer.validate_and_handle()

            @bindings.add("escape", "enter")
            def newline(event) -> None:  # type: ignore[no-untyped-def]
                event.current_buffer.insert_text("\n")

            @bindings.add("tab")
            def complete(event) -> None:  # type: ignore[no-untyped-def]
                buffer = event.current_buffer
                if buffer.complete_state is None:
                    buffer.start_completion(select_first=True)
                else:
                    buffer.complete_next()

            self._session = PromptSession(
                history=InMemoryHistory(),
                key_bindings=bindings,
                multiline=True,
                mouse_support=self._mouse,
                enable_history_search=True,
                completer=SlashCommandCompleter(commands),
                complete_while_typing=True,
                auto_suggest=AutoSuggestFromHistory(),
            )

    @property
    def mouse_enabled(self) -> bool:
        return self._mouse

    def toggle_mouse(self) -> bool:
        """Переключает mouse reporting; возвращает новое состояние."""
        self._mouse = not self._mouse
        if self._session is not None:
            self._session.mouse_support = self._mouse
        return self._mouse

    async def read(self, prompt: str) -> str:
        if self._session is None:
            loop = asyncio.get_running_loop()
            reader = self._fallback or input
            return await loop.run_in_executor(None, lambda: reader(prompt))
        return await self._session.prompt_async(
            ANSI(prompt),
            prompt_continuation=lambda width, _line, _soft: " " * max(0, width - 2) + "· ",
            wrap_lines=True,
        )
