"""Gemini CLI provider: subprocess runtime поверх pure stream parser."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from core import config, db, projects
from providers.models import ProgressCallback
from providers.parsers.gemini import GeminiStreamParser
from providers.process import finish_process, resolve_cli_argv, write_stdin

from .base import NO_WINDOW, CLIProvider

log = logging.getLogger("bridge.provider.gemini")


def _maybe_dump_event(line: bytes) -> None:
    if os.environ.get("GEMINI_DEBUG_STREAM", "").strip() not in ("1", "true", "yes"):
        return
    try:
        dump_dir = config.LOGS_DIR
        dump_dir.mkdir(parents=True, exist_ok=True)
        if not hasattr(_maybe_dump_event, "_path"):
            _maybe_dump_event._path = dump_dir / f"gemini-stream-{int(time.time())}.jsonl"
        with open(_maybe_dump_event._path, "ab") as file:
            file.write(line if line.endswith(b"\n") else line + b"\n")
    except Exception:
        # Debug dump — best effort и не должен ронять основной ответ.
        pass


def _encode_cwd(cwd: str) -> str:
    """Кодирует cwd в имя папки так же, как Claude CLI."""
    return str(Path(cwd).resolve()).replace(":", "-").replace("\\", "-").replace("/", "-")


def _owned_claude_home(user_id: int) -> Path | None:
    """Возвращает только Claude-профиль того же владельца; shared требует явного выбора."""
    with db.conn() as connection:
        account = connection.execute(
            """SELECT cli_home_path FROM accounts
               WHERE provider='claude_code' AND enabled=1 AND owner_user_id=?
               ORDER BY id LIMIT 1""",
            (user_id,),
        ).fetchone()
    return Path(account["cli_home_path"]) if account else None


def _cwd_belongs_to_user_project(user_id: int, cwd: str) -> bool:
    try:
        resolved = Path(cwd).resolve(strict=True)
    except OSError:
        return False
    for project in projects.list_accessible_projects(user_id):
        try:
            root = Path(project["root_path"]).resolve(strict=True)
        except OSError:
            continue
        if resolved == root or resolved.is_relative_to(root):
            return True
    return False


def _load_claude_memory(claude_home: Path | None, cwd: str) -> str:
    """Читает memory только из заранее авторизованного Claude-профиля."""
    if claude_home is None:
        return ""
    encoded = _encode_cwd(cwd)
    memory_dir = claude_home / "projects" / encoded / "memory"
    index = memory_dir / "MEMORY.md"
    if not index.exists():
        return ""
    parts = [
        "# Память пользователя\n",
        f"## Индекс (MEMORY.md)\n{index.read_text(encoding='utf-8').strip()}\n",
    ]
    for markdown_file in sorted(memory_dir.glob("*.md")):
        if markdown_file.name == "MEMORY.md":
            continue
        content = markdown_file.read_text(encoding="utf-8").strip()
        parts.append(f"## {markdown_file.name}\n{content}\n")
    return "\n".join(parts)


def _short_tool_desc(name: str, parameters: dict) -> str:
    """Строит короткое описание вызова Gemini-инструмента."""
    if not isinstance(parameters, dict):
        parameters = {}

    def short(value: object, limit: int = 70) -> str:
        text = str(value).replace("\n", " ").replace("\r", " ").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def file_name(path: object) -> str:
        if not path:
            return "?"
        try:
            return Path(str(path)).name or str(path)
        except Exception:
            return str(path)

    path = parameters.get("file_path") or parameters.get("path") or parameters.get("absolute_path")
    if name in ("read_file", "ReadFile"):
        return f"Read {file_name(path)}"
    if name in ("write_file", "WriteFile"):
        return f"Write {file_name(path)}"
    if name in ("edit_file", "replace", "Edit", "edit"):
        return f"Edit {file_name(path)}"
    if name == "read_many_files":
        paths = parameters.get("paths") or parameters.get("file_paths") or []
        return (
            f"ReadMany ({len(paths)} файлов)" if isinstance(paths, list) and paths else "ReadMany"
        )
    if name in ("glob", "Glob", "list_files", "ls"):
        return f"Glob {short(parameters.get('pattern') or parameters.get('path'), 60)}"
    if name in ("grep", "search_file_content", "Grep"):
        return f"Grep '{short(parameters.get('pattern') or parameters.get('query'), 50)}'"
    if name in ("run_shell_command", "shell", "Bash", "execute_shell_command"):
        return f"Shell: {short(parameters.get('command') or parameters.get('cmd'), 80)}"
    if name in ("web_fetch", "WebFetch"):
        return f"WebFetch {short(parameters.get('url') or parameters.get('prompt'), 60)}"
    if name in ("web_search", "google_web_search", "WebSearch"):
        return f"WebSearch '{short(parameters.get('query'), 50)}'"
    if name == "save_memory":
        return f"SaveMemory: {short(parameters.get('fact') or parameters.get('memory'), 60)}"
    if name == "update_topic":
        title = parameters.get("title") or parameters.get("summary") or ""
        return f"План: {short(title, 60)}"
    if parameters:
        try:
            for value in parameters.values():
                if isinstance(value, str) and value.strip():
                    return f"{name}: {short(value, 60)}"
        except Exception:
            pass
    return name


class GeminiProvider(CLIProvider):
    provider_name = "gemini"

    def env(self) -> dict:
        environment = super().env()
        environment["HOME"] = str(self.cli_home)
        environment["USERPROFILE"] = str(self.cli_home)
        environment["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
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
        sections = [config.RU_SYSTEM_INSTRUCTION]
        memory = ""
        if self.user_id is not None and _cwd_belongs_to_user_project(self.user_id, cwd):
            memory = _load_claude_memory(_owned_claude_home(self.user_id), cwd)
        if memory:
            sections.append(
                "Ниже — память пользователя (накоплена Claude в этом же cwd). "
                "Используй её для контекста: кто пользователь, его предпочтения, "
                "текущие проекты. Память доступна только для чтения — изменять её "
                "ты не можешь.\n\n" + memory
            )
            log.info("gemini: injected memory (%d chars)", len(memory))
        sections.append("# Запрос пользователя\n" + (prompt or ""))
        if attachments:
            paths = "\n".join(f"- {path}" for path in attachments)
            sections.append(f"# Прикреплённые файлы\n{paths}")
        full_prompt = "\n\n---\n\n".join(sections)

        argv = [
            "gemini",
            "--skip-trust",
            "--approval-mode",
            "yolo",
            "-o",
            "stream-json",
            "-p",
            "",
        ]
        if model:
            argv += ["-m", model]
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
        argv = resolve_cli_argv(list(argv), allow_powershell=True)
        log.info("exec %s (stream)", argv[0])
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self.env(),
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024,
            creationflags=NO_WINDOW,
        )
        await write_stdin(proc, stdin_data)

        parser = GeminiStreamParser(_short_tool_desc, session_id=session_id)
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
                log.info("cancel: killed gemini subprocess pid=%s", proc.pid)
            raise
        finally:
            await finish_process(proc)

        stderr = "".join(stderr_buffer)
        log.info(
            "exec gemini stream done | rc=%s | text_chars=%d | err_chars=%d | events=%s",
            proc.returncode,
            len(parser.text),
            len(stderr),
            parser.events_seen,
        )
        if proc.returncode and proc.returncode != 0:
            raise RuntimeError(f"gemini failed (rc={proc.returncode}): {stderr[:2000]}")
        return parser.provider_result().as_tuple()
