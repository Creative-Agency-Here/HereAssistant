"""Qwen Code provider: изолированный CLI runtime с нативным stream-json."""

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
from .claude_code import (
    _extract_text_from_message,
    _extract_thinking,
    _result_preview,
)
from .gemini import _short_tool_desc

ALLOWED_APPROVAL_MODES = frozenset({"plan", "default", "auto-edit", "auto"})
INHERITED_PROVIDER_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "BAILIAN_CODING_PLAN_API_KEY",
        "DASHSCOPE_API_KEY",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "QWEN_MODEL",
    }
)


def _approval_mode() -> str:
    """Не допускает YOLO в Telegram-шлюзе без отдельной sandbox-гарантии."""
    requested = os.environ.get("QWEN_APPROVAL_MODE", "auto").strip()
    if requested in ALLOWED_APPROVAL_MODES:
        return requested
    log.warning("Небезопасный QWEN_APPROVAL_MODE=%r заменён на auto", requested)
    return "auto"


def _maybe_dump_event(line: bytes) -> None:
    if os.environ.get("QWEN_DEBUG_STREAM", "").strip() not in ("1", "true", "yes"):
        return
    try:
        dump_dir = config.LOGS_DIR
        dump_dir.mkdir(parents=True, exist_ok=True)
        if not hasattr(_maybe_dump_event, "_path"):
            _maybe_dump_event._path = dump_dir / f"qwen-stream-{int(time.time())}.jsonl"
        with open(_maybe_dump_event._path, "ab") as file:
            file.write(line if line.endswith(b"\n") else line + b"\n")
    except OSError:
        # Диагностический дамп не должен ломать ответ пользователя.
        pass


class QwenCodeProvider(CLIProvider):
    provider_name = "qwen_code"

    def env(self) -> dict[str, str]:
        environment = super().env()
        # Глобальный ключ процесса не должен незаметно подменять выбранный аккаунт.
        for variable in INHERITED_PROVIDER_ENV:
            environment.pop(variable, None)
        qwen_home = self.cli_home / ".qwen"
        qwen_home.mkdir(parents=True, exist_ok=True)
        environment["QWEN_HOME"] = str(qwen_home)
        environment["QWEN_RUNTIME_DIR"] = str(qwen_home)
        environment["QWEN_TELEMETRY_ENABLED"] = "false"
        return environment

    async def run(
        self,
        prompt: str,
        cwd: str,
        session_id: str | None,
        model: str | None,
        attachments: list[Path] | None = None,
        progress: ProgressCallback | None = None,
    ):
        full_prompt = prompt or ""
        if attachments:
            paths = "\n".join(f"- {path}" for path in attachments)
            full_prompt += f"\n\n[Прикреплённые пользователем файлы — абсолютные пути]\n{paths}\n"

        argv = [
            "qwen",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--approval-mode",
            _approval_mode(),
            "--append-system-prompt",
            config.RU_SYSTEM_INSTRUCTION,
            "--prompt",
            "",
        ]
        if model:
            argv += ["--model", model]
        if session_id:
            argv += ["--resume", session_id]

        return await self._run_streaming(argv, cwd, session_id, progress, stdin_data=full_prompt)

    async def _run_streaming(
        self,
        argv: list[str],
        cwd: str,
        session_id: str | None,
        progress: ProgressCallback | None,
        stdin_data: str | None = None,
    ):
        argv = resolve_cli_argv(list(argv), allow_powershell=True)
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
            except (OSError, RuntimeError, TypeError, ValueError) as error:
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
                    raise RuntimeError(
                        f"CLI stream timeout after {config.CLI_TIMEOUT}s"
                    ) from error
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
                log.info("cancel: killed qwen subprocess pid=%s", proc.pid)
            raise
        finally:
            await finish_process(proc)

        stderr = "".join(stderr_buffer)
        log.info(
            "exec qwen stream done | rc=%s | text_chars=%d | err_chars=%d | events=%s",
            proc.returncode,
            len(parser.text),
            len(stderr),
            parser.events_seen,
        )
        if (proc.returncode and proc.returncode != 0) or parser.error_subtype:
            reason = stderr.strip() or parser.error_text or parser.error_subtype or "нет деталей"
            raise RuntimeError(f"qwen failed (rc={proc.returncode}): {reason[:2000]}")
        return parser.provider_result().as_tuple()
