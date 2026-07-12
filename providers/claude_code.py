"""Claude Code provider: тонкий subprocess runtime поверх pure stream parser."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from core import config
from providers.models import ProgressCallback
from providers.parsers.claude import ClaudeStreamParser
from providers.process import finish_process, resolve_cli_argv, write_stdin

from .base import CLIProvider, log

ALLOWED_PERMISSION_MODES = frozenset({"acceptEdits", "default"})


def _permission_mode() -> str:
    requested = os.environ.get("CLAUDE_PERMISSION_MODE", "acceptEdits").strip()
    if requested in ALLOWED_PERMISSION_MODES:
        return requested
    log.warning("Запрещённый CLAUDE_PERMISSION_MODE=%r заменён на acceptEdits", requested)
    return "acceptEdits"


def _maybe_dump_event(line: bytes) -> None:
    """Опционально сохраняет сырой stream-json без влияния на основной parser."""
    if os.environ.get("CLAUDE_DEBUG_STREAM", "").strip() not in ("1", "true", "yes"):
        return
    try:
        dump_dir = config.LOGS_DIR
        dump_dir.mkdir(parents=True, exist_ok=True)
        if not hasattr(_maybe_dump_event, "_path"):
            _maybe_dump_event._path = dump_dir / f"claude-stream-{int(time.time())}.jsonl"
        with open(_maybe_dump_event._path, "ab") as file:
            file.write(line if line.endswith(b"\n") else line + b"\n")
    except Exception:
        # Debug dump — best effort и не должен ронять основной ответ.
        pass


def _extract_text_from_block(block: dict) -> str:
    """Извлекает текст из content-блока разных stream-json форматов."""
    if not isinstance(block, dict):
        return ""
    block_type = block.get("type")
    if block_type == "text":
        return block.get("text", "") or ""
    if block_type == "text_delta":
        return block.get("text", "") or block.get("delta", "") or ""
    return ""


def _extract_text_from_message(message: dict) -> str:
    """Извлекает весь текст из message.content."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_extract_text_from_block(block) for block in content)
    return ""


def _extract_thinking(block: dict) -> str:
    """Извлекает extended thinking из полного блока или delta."""
    if not isinstance(block, dict):
        return ""
    block_type = block.get("type")
    if block_type == "thinking":
        return block.get("thinking", "") or ""
    if block_type == "thinking_delta":
        return block.get("thinking", "") or block.get("delta", "") or ""
    return ""


def _result_preview(content: object, limit: int = 200) -> str:
    """Возвращает только первую непустую строку результата и число остальных."""
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", "") if block.get("type") == "text" else "")
            elif isinstance(block, str):
                parts.append(block)
        text = "\n".join(parts)
    text = text.strip()
    if not text:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    head = lines[0][:limit] if lines else ""
    extra = f" (+{len(lines) - 1} стр.)" if len(lines) > 1 else ""
    return head + extra


def _short_tool_desc(name: str, tool_input: dict) -> str:
    """Строит короткое безопасное описание вызова Claude-инструмента."""
    if not isinstance(tool_input, dict):
        tool_input = {}

    def short(value: object, limit: int = 70) -> str:
        if value is None:
            return ""
        text = str(value).replace("\n", " ").replace("\r", " ").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def file_name(path: object) -> str:
        if not path:
            return "?"
        try:
            return Path(str(path)).name or str(path)
        except Exception:
            return str(path)

    if name == "Read":
        return f"Read {file_name(tool_input.get('file_path') or tool_input.get('filePath'))}"
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("filePath")
        return f"{name} {file_name(path)}"
    if name == "Glob":
        return f"Glob {short(tool_input.get('pattern'), 60)}"
    if name == "Grep":
        return f"Grep '{short(tool_input.get('pattern'), 50)}'"
    if name in ("PowerShell", "Bash"):
        return f"{name}: {short(tool_input.get('command'), 80)}"
    if name == "TaskCreate":
        return f"TaskCreate '{short(tool_input.get('subject'), 50)}'"
    if name == "TaskUpdate":
        return f"TaskUpdate #{tool_input.get('taskId', '?')} → {tool_input.get('status', '—')}"
    if name == "TaskGet":
        return f"TaskGet #{tool_input.get('taskId', '?')}"
    if name == "WebFetch":
        return f"WebFetch {short(tool_input.get('url'), 60)}"
    if name == "WebSearch":
        return f"WebSearch '{short(tool_input.get('query'), 50)}'"
    if name == "Agent":
        return f"Agent: {short(tool_input.get('description'), 50)}"
    if name == "ToolSearch":
        return f"ToolSearch {short(tool_input.get('query'), 50)}"
    if name == "Skill":
        return f"Skill /{tool_input.get('skill', '?')}"
    if tool_input:
        try:
            first_value = next(iter(tool_input.values()), "")
            if isinstance(first_value, str) and first_value:
                return f"{name}: {short(first_value, 60)}"
        except Exception:
            pass
    return name


class ClaudeCodeProvider(CLIProvider):
    provider_name = "claude_code"

    def env(self) -> dict:
        environment = super().env()
        environment["CLAUDE_CONFIG_DIR"] = str(self.cli_home)
        return environment

    async def run(
        self,
        prompt,
        cwd,
        session_id,
        model,
        attachments=None,
        progress: ProgressCallback | None = None,
    ):
        full_prompt = prompt or ""
        if attachments:
            paths = "\n".join(f"- {path}" for path in attachments)
            full_prompt += f"\n\n[Прикреплённые пользователем файлы — абсолютные пути]\n{paths}\n"

        permission_mode = _permission_mode()
        argv = [
            "claude",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            permission_mode,
            "--append-system-prompt",
            config.RU_SYSTEM_INSTRUCTION,
        ]
        if model:
            argv += ["--model", model]
        if session_id:
            argv += ["--resume", session_id]

        return await self._run_streaming(
            argv,
            cwd,
            session_id,
            progress,
            stdin_data=full_prompt,
        )

    async def _run_streaming(
        self,
        argv,
        cwd,
        session_id,
        progress: ProgressCallback | None,
        stdin_data: str | None = None,
    ):
        argv = resolve_cli_argv(list(argv))
        log.info("exec %s (stream)", argv[0])

        proc = await self._spawn(
            argv,
            cwd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            limit=32 * 1024 * 1024,
        )
        await write_stdin(proc, stdin_data)

        parser = ClaudeStreamParser(
            text_from_message=_extract_text_from_message,
            thinking_from_block=_extract_thinking,
            result_preview=_result_preview,
            tool_description=_short_tool_desc,
            session_id=session_id,
        )
        stderr_buffer: list[str] = []

        async def emit_progress(event_type: str) -> None:
            if not progress:
                return
            try:
                await progress(parser.text, event_type, parser.progress_meta())
            except Exception as error:
                log.warning("progress callback error: %s", error)

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while line := await proc.stderr.readline():
                stderr_buffer.append(line.decode(errors="replace"))

        async def read_stdout() -> None:
            assert proc.stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=config.CLI_TIMEOUT
                    )
                except asyncio.TimeoutError as error:
                    raise RuntimeError(f"CLI stream timeout after {config.CLI_TIMEOUT}s") from error
                if not line:
                    break
                _maybe_dump_event(line)
                try:
                    event = json.loads(line.decode(errors="replace").strip())
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                for update in parser.consume(event):
                    await emit_progress(update)

        try:
            await asyncio.gather(read_stdout(), read_stderr())
        except asyncio.CancelledError:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                log.info("cancel: killed claude subprocess pid=%s", proc.pid)
            raise
        finally:
            await finish_process(proc)

        stderr = "".join(stderr_buffer)
        log.info(
            "exec claude stream done | rc=%s | text_chars=%d | err_chars=%d | "
            "rate_limit_hits=%s error_subtype=%s events=%s",
            proc.returncode,
            len(parser.text),
            len(stderr),
            parser.rate_limit_hits,
            parser.error_subtype,
            parser.events_seen,
        )

        if proc.returncode and proc.returncode != 0:
            if parser.text and parser.rate_limit_hits:
                return parser.partial_rate_limit_result().as_tuple()
            raise RuntimeError(
                f"claude failed (rc={proc.returncode}): {parser.error_reason(stderr)}"
            )
        return parser.provider_result().as_tuple()
