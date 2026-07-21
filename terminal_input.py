"""Multiline terminal input with click-to-position and soft wrapping."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings


class TerminalPrompt:
    """Claude-like editor: Enter sends, Alt+Enter adds a line, paste stays multiline.

    Mouse reporting lets a click move the caret inside the editable prompt.
    VS Code still exposes native terminal selection while Shift is held.
    """

    def __init__(
        self,
        *,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        fallback: Callable[[str], str] | None = None,
    ) -> None:
        self._fallback = fallback
        self._interactive = bool(input_stream.isatty() and output_stream.isatty())
        self._session: PromptSession[str] | None = None
        if self._interactive:
            bindings = KeyBindings()

            @bindings.add("enter")
            def submit(event) -> None:  # type: ignore[no-untyped-def]
                event.current_buffer.validate_and_handle()

            @bindings.add("escape", "enter")
            def newline(event) -> None:  # type: ignore[no-untyped-def]
                event.current_buffer.insert_text("\n")

            self._session = PromptSession(
                history=InMemoryHistory(),
                key_bindings=bindings,
                multiline=True,
                mouse_support=True,
                enable_history_search=True,
            )

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
