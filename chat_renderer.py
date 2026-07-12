"""Типизированный потоковый renderer терминального чата."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TextIO

TTY = sys.stdout.isatty()


def _color(code: str) -> str:
    return code if TTY else ""


G = _color("\033[92m")
R = _color("\033[91m")
Y = _color("\033[93m")
C = _color("\033[96m")
M = _color("\033[95m")
W = _color("\033[97m")
B = _color("\033[1m")
D = _color("\033[2m")
ITALIC = _color("\033[3m")
X = _color("\033[0m")

STEP_ICON = {"run": f"{Y}⏺{X}", "ok": f"{G}✓{X}", "err": f"{R}✗{X}"}


class MdStream:
    """Потоково преобразует ограниченный Markdown в ANSI без утечки токенов."""

    def __init__(self) -> None:
        self.bold = False
        self.code = False
        self.fence = False
        self.heading = False
        self.line_start = True
        self.tail = ""

    def _style(self) -> str:
        style = X
        if self.fence or self.code:
            style += C
        if self.heading:
            style += B + M
        if self.bold:
            style += B
        return style

    def feed(self, chunk: str) -> str:
        source = self.tail + chunk
        self.tail = ""
        output: list[str] = []
        index = 0
        while index < len(source):
            char = source[index]
            remaining = len(source) - index
            if char == "\n":
                self.heading = False
                self.code = False
                output.append(X + "\n")
                self.line_start = True
                if self.fence or self.bold:
                    output.append(self._style())
                index += 1
                continue
            if char == "`" and self.line_start:
                if remaining < 3:
                    self.tail = source[index:]
                    break
                if source[index : index + 3] == "```":
                    newline = source.find("\n", index)
                    if newline == -1:
                        self.tail = source[index:]
                        break
                    self.fence = not self.fence
                    output.append(self._style())
                    index = newline + 1
                    continue
            if char == "`" and not self.fence:
                self.code = not self.code
                output.append(self._style())
                index += 1
                self.line_start = False
                continue
            if self.fence or self.code:
                output.append(char)
                index += 1
                self.line_start = False
                continue
            if char == "*":
                if remaining == 1:
                    self.tail = source[index:]
                    break
                if source[index + 1] == "*":
                    self.bold = not self.bold
                    output.append(self._style())
                    index += 2
                    self.line_start = False
                    continue
                if self.line_start and source[index + 1] == " ":
                    output.append(f"{M}•{self._style()} ")
                    index += 2
                    self.line_start = False
                    continue
                output.append(char)
                index += 1
                self.line_start = False
                continue
            if char == "-" and self.line_start:
                if remaining == 1:
                    self.tail = source[index:]
                    break
                if source[index + 1] == " ":
                    output.append(f"{M}•{self._style()} ")
                    index += 2
                    self.line_start = False
                    continue
            if char == "#" and self.line_start:
                end = index
                while end < len(source) and source[end] == "#":
                    end += 1
                if end >= len(source):
                    self.tail = source[index:]
                    break
                if end - index <= 4 and source[end] == " ":
                    self.heading = True
                    output.append(self._style())
                    index = end + 1
                    self.line_start = False
                    continue
            output.append(char)
            if char != " ":
                self.line_start = False
            index += 1
        return "".join(output)

    def close(self) -> str:
        tail, self.tail = self.tail, ""
        needs_reset = tail or self.bold or self.code or self.fence or self.heading
        return tail + X if needs_reset else ""


@dataclass(slots=True)
class ProgressRenderState:
    thinking_len: int = 0
    thinking_shown: bool = False
    printed_tools: set[str] = field(default_factory=set)
    pending_text: str = ""
    text_len: int = 0
    text_prefix: str = ""
    answer_started: bool = False
    markdown: MdStream = field(default_factory=MdStream)


def make_progress(state: ProgressRenderState, *, output: TextIO = sys.stdout):
    async def progress(text: str, event_type: str, meta: Mapping[str, Any]) -> None:
        thinking = str(meta.get("thinking") or "")
        if len(thinking) > state.thinking_len:
            chunk = thinking[state.thinking_len :]
            state.thinking_len = len(thinking)
            # После начала ответа reasoning больше не печатаем: иначе его дельта
            # вклинивается между text-delta и визуально повреждает ответ.
            if not state.answer_started:
                if not state.thinking_shown:
                    output.write(f"\n{D}{ITALIC}💭 ")
                    state.thinking_shown = True
                output.write(f"{D}{ITALIC}{chunk}{X}")

        raw_steps = meta.get("steps") or []
        steps = raw_steps if isinstance(raw_steps, list) else []
        for index, raw_step in enumerate(steps):
            step = raw_step if isinstance(raw_step, Mapping) else {}
            key = str(step.get("id") or f"i{index}")
            done = step.get("status") != "run" or step.get("result")
            if done and key not in state.printed_tools:
                state.printed_tools.add(key)
                flush_text(state, output=output)
                icon = STEP_ICON.get(str(step.get("status")), f"{Y}⏺{X}")
                output.write(f"\n{icon} {W}{step.get('desc')}{X}")
                result = step.get("result")
                if result:
                    preview = str(result)
                    if len(preview) > 400:
                        preview = preview[:400] + "…"
                    output.write(f"\n   {D}⎿ {preview}{X}")
        if event_type in ("assistant_delta", "partial_delta") and text:
            state.pending_text = text
            flush_text(state, output=output)
        output.flush()

    return progress


def flush_text(state: ProgressRenderState, *, output: TextIO = sys.stdout) -> None:
    text = state.pending_text
    shown = state.text_len
    if len(text) <= shown:
        return
    if not text.startswith(state.text_prefix):
        output.write("\n")
        shown = 0
        state.markdown = MdStream()
    if not state.answer_started:
        output.write(f"\n{C}▌{X} ")
        state.answer_started = True
    output.write(state.markdown.feed(text[shown:]))
    state.text_len = len(text)
    state.text_prefix = text[:200]


def finish_stream(
    state: ProgressRenderState,
    final_text: str,
    *,
    output: TextIO = sys.stdout,
) -> None:
    if final_text:
        state.pending_text = final_text
        flush_text(state, output=output)
    output.write(state.markdown.close())


def format_run_summary(meta: Mapping[str, Any], duration: float) -> str:
    parts = [f"{duration:.0f}с"]
    raw_edits = meta.get("edits") or []
    edits = raw_edits if isinstance(raw_edits, list) else []
    if edits:
        added = sum(_integer(edit.get("added")) for edit in edits if isinstance(edit, Mapping))
        removed = sum(_integer(edit.get("removed")) for edit in edits if isinstance(edit, Mapping))
        parts.append(f"{G}+{added}{X} {R}−{removed}{X} в {len(edits)} файл.")
    tokens_in = meta.get("tokens_in")
    tokens_out = meta.get("tokens_out")
    if tokens_in or tokens_out:
        parts.append(f"токены {tokens_in or 0}/{tokens_out or 0}")
    return f"\n{D}— {' · '.join(parts)}{X}\n"


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
