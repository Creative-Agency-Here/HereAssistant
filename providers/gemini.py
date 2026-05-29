"""Gemini CLI provider — со стримингом stream-json, прогрессом и tool tracking."""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from core import config
from .base import CLIProvider, NO_WINDOW

import logging
log = logging.getLogger("bridge.provider.gemini")


ProgressCb = Callable[[str, str, dict], Awaitable[None]]


# Если переменная окружения GEMINI_DEBUG_STREAM=1 — дампим
# сырые events в .runtime/logs/gemini-stream-<timestamp>.jsonl для отладки.
def _maybe_dump_event(line: bytes):
    if os.environ.get("GEMINI_DEBUG_STREAM", "").strip() not in ("1", "true", "yes"):
        return
    try:
        dump_dir = config.LOGS_DIR
        dump_dir.mkdir(parents=True, exist_ok=True)
        if not hasattr(_maybe_dump_event, "_path"):
            _maybe_dump_event._path = dump_dir / f"gemini-stream-{int(time.time())}.jsonl"
        with open(_maybe_dump_event._path, "ab") as f:
            f.write(line if line.endswith(b"\n") else line + b"\n")
    except Exception:
        pass


def _encode_cwd(cwd: str) -> str:
    """Кодирует cwd в имя папки, как это делает Claude CLI: C:\\X → C--X."""
    return str(Path(cwd).resolve()).replace(":", "-").replace("\\", "-").replace("/", "-")


def _load_claude_memory(cli_homes_root: Path, cwd: str) -> str:
    """Читает память Claude для данного cwd и склеивает её в один текст."""
    encoded = _encode_cwd(cwd)
    for claude_home in sorted(cli_homes_root.glob("claude_code__*")):
        memory_dir = claude_home / "projects" / encoded / "memory"
        index = memory_dir / "MEMORY.md"
        if not index.exists():
            continue

        parts = [f"# Память пользователя\n",
                 f"## Индекс (MEMORY.md)\n{index.read_text(encoding='utf-8').strip()}\n"]
        for md in sorted(memory_dir.glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            parts.append(f"## {md.name}\n{md.read_text(encoding='utf-8').strip()}\n")
        return "\n".join(parts)
    return ""


def _short_tool_desc(name: str, params: dict) -> str:
    """Короткое описание вызова Gemini-инструмента: имя + ключевые аргументы."""
    if not isinstance(params, dict):
        params = {}

    def _short(s, n=70):
        s = str(s).replace("\n", " ").replace("\r", " ").strip()
        return s if len(s) <= n else s[: n - 1] + "…"

    def _name(p):
        if not p:
            return "?"
        try:
            return Path(str(p)).name or str(p)
        except Exception:
            return str(p)

    # Маппинг известных Gemini-инструментов (snake_case) на читаемые описания
    if name in ("read_file", "ReadFile"):
        return f"Read {_name(params.get('file_path') or params.get('path') or params.get('absolute_path'))}"
    if name in ("write_file", "WriteFile"):
        return f"Write {_name(params.get('file_path') or params.get('path') or params.get('absolute_path'))}"
    if name in ("edit_file", "replace", "Edit", "edit"):
        return f"Edit {_name(params.get('file_path') or params.get('path') or params.get('absolute_path'))}"
    if name in ("read_many_files",):
        paths = params.get("paths") or params.get("file_paths") or []
        if isinstance(paths, list) and paths:
            return f"ReadMany ({len(paths)} файлов)"
        return "ReadMany"
    if name in ("glob", "Glob", "list_files", "ls"):
        return f"Glob {_short(params.get('pattern') or params.get('path'), 60)}"
    if name in ("grep", "search_file_content", "Grep"):
        return f"Grep '{_short(params.get('pattern') or params.get('query'), 50)}'"
    if name in ("run_shell_command", "shell", "Bash", "execute_shell_command"):
        return f"Shell: {_short(params.get('command') or params.get('cmd'), 80)}"
    if name in ("web_fetch", "WebFetch"):
        return f"WebFetch {_short(params.get('url') or params.get('prompt'), 60)}"
    if name in ("web_search", "google_web_search", "WebSearch"):
        return f"WebSearch '{_short(params.get('query'), 50)}'"
    if name in ("save_memory",):
        return f"SaveMemory: {_short(params.get('fact') or params.get('memory'), 60)}"
    if name == "update_topic":
        # Внутренний инструмент планирования Gemini — показываем как «План: …»
        title = params.get("title") or params.get("summary") or ""
        return f"План: {_short(title, 60)}"
    # Дженерик: имя инструмента + первое строковое значение
    if params:
        try:
            for v in params.values():
                if isinstance(v, str) and v.strip():
                    return f"{name}: {_short(v, 60)}"
        except Exception:
            pass
    return name


class GeminiProvider(CLIProvider):
    provider_name = "gemini"

    def env(self) -> dict:
        e = super().env()
        e["HOME"] = str(self.cli_home)
        e["USERPROFILE"] = str(self.cli_home)
        e["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        return e

    async def run(self, prompt, cwd, session_id, model, attachments=None,
                  progress: Optional[ProgressCb] = None):
        # У Gemini CLI в non-interactive (-p) нет отдельного флага для system prompt,
        # поэтому всё (инструкцию + память) кладём в начало пользовательского промпта.
        sections = [config.RU_SYSTEM_INSTRUCTION]

        memory = _load_claude_memory(self.cli_home.parent, cwd)
        if memory:
            sections.append(
                "Ниже — память пользователя (накоплена Claude в этом же cwd). "
                "Используй её для контекста: кто пользователь, его предпочтения, "
                "текущие проекты. Память доступна только для чтения — изменять её "
                "ты не можешь.\n\n" + memory
            )
            log.info("gemini: injected memory (%d chars) for cwd=%s", len(memory), cwd)

        sections.append("# Запрос пользователя\n" + (prompt or ""))

        if attachments:
            paths = "\n".join(f"- {p}" for p in attachments)
            sections.append(f"# Прикреплённые файлы\n{paths}")

        full_prompt = "\n\n---\n\n".join(sections)

        # -p со значением "" + промпт через stdin: документация говорит "Appended to
        # input on stdin (if any)" — stdin становится основной частью промпта, а -p
        # переключает CLI в headless-режим.
        argv = [
            "gemini",
            "--skip-trust",                  # cwd может быть untrusted (например, C:\Users\Administrator)
            "--approval-mode", "yolo",       # авто-аппрув всех tool calls (заменяет старый -y)
            "-o", "stream-json",             # построчный JSON
            "-p", "",                        # включить headless; реальный текст пойдёт через stdin
        ]
        if model:
            argv += ["-m", model]
        # session_id Gemini пока не пробрасываем — у CLI 0.43 --resume принимает
        # только индекс/"latest", а --session-id создаёт НОВУЮ сессию с заданным
        # UUID (не продолжает существующую). Историю поддерживаем через
        # build_prompt_with_history в handlers, как и раньше.

        return await self._run_streaming(argv, cwd, session_id, progress,
                                          stdin_data=full_prompt)

    async def _run_streaming(self, argv, cwd, session_id, progress: Optional[ProgressCb],
                             stdin_data: Optional[str] = None):
        argv = list(argv)
        if os.name == "nt":
            resolved = shutil.which(argv[0])
            if resolved is None:
                raise RuntimeError(f"Не найдена команда '{argv[0]}'")
            if resolved.lower().endswith((".cmd", ".bat")):
                argv = ["cmd", "/c", resolved, *argv[1:]]
            elif resolved.lower().endswith(".ps1"):
                argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", resolved, *argv[1:]]
            else:
                argv[0] = resolved

        log.info("exec %s in %s (stream)", argv[0], cwd)

        # limit=32 МБ — одна JSON-строка может быть огромной (tool_result с большим файлом)
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=self.env(),
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024,
            creationflags=NO_WINDOW,
        )

        if stdin_data is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_data.encode("utf-8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        state = {
            "text": "",                       # накопленный текст ответа
            "session": session_id,            # session_id из init-события
            "meta": {},                       # tokens_in, tokens_out
            "edits": [],                      # tool_use правки (write_file/edit_file/replace)
            "tool_uses": [],                  # имена всех tool_use событий
            "tool_call_log": [],              # подробное описание каждого вызова
            "tool_call_idx": {},              # tool_id -> индекс в tool_call_log
            "current_tool": None,
        }
        stderr_buf = []
        events_seen = {}

        def _record_tool_call(name: str, params: dict, tool_id: Optional[str]):
            desc = _short_tool_desc(name, params or {})
            if tool_id and tool_id in state["tool_call_idx"]:
                state["tool_call_log"][state["tool_call_idx"][tool_id]] = desc
                return
            state["tool_call_log"].append(desc)
            if tool_id:
                state["tool_call_idx"][tool_id] = len(state["tool_call_log"]) - 1

        async def emit_progress(event_type: str):
            if not progress:
                return
            try:
                meta_snapshot = {
                    "edits": list(state["edits"]),
                    "tool_uses": list(state["tool_uses"]),
                    "tool_call_log": list(state["tool_call_log"]),
                    "current_tool": state["current_tool"],
                }
                await progress(state["text"], event_type, meta_snapshot)
            except Exception as e:
                log.warning("progress callback error: %s", e)

        async def read_stderr():
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_buf.append(line.decode(errors="replace"))

        async def read_stdout():
            assert proc.stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(),
                                                  timeout=config.CLI_TIMEOUT)
                except asyncio.TimeoutError:
                    raise RuntimeError(f"CLI stream timeout after {config.CLI_TIMEOUT}s")
                if not line:
                    break

                _maybe_dump_event(line)

                try:
                    evt = json.loads(line.decode(errors="replace").strip())
                except json.JSONDecodeError:
                    continue

                etype = evt.get("type", "unknown")
                events_seen[etype] = events_seen.get(etype, 0) + 1

                # ---------- init ----------
                if etype == "init":
                    state["session"] = evt.get("session_id") or state["session"]
                    continue

                # ---------- message ----------
                if etype == "message":
                    role = evt.get("role")
                    if role == "user":
                        # эхо нашего запроса, пропускаем
                        continue
                    if role == "assistant":
                        content = evt.get("content", "") or ""
                        if evt.get("delta"):
                            # partial chunk — аккумулируем
                            state["text"] += content
                        else:
                            # полное сообщение целиком — заменяем (на случай non-delta мода)
                            state["text"] = content
                        await emit_progress("partial_delta")
                    continue

                # ---------- tool_use ----------
                if etype == "tool_use":
                    name = evt.get("tool_name") or evt.get("name") or "?"
                    tool_id = evt.get("tool_id") or evt.get("id")
                    params = evt.get("parameters") or evt.get("input") or {}
                    if not isinstance(params, dict):
                        params = {}
                    state["current_tool"] = name
                    state["tool_uses"].append(name)
                    _record_tool_call(name, params, tool_id)

                    # Edit tracking для write_file/edit_file/replace
                    if name in ("write_file", "edit_file", "replace"):
                        old = (params.get("old_string") or params.get("old_str")
                               or params.get("oldText") or "")
                        new = (params.get("new_string") or params.get("new_str")
                               or params.get("newText") or params.get("content") or "")
                        added = new.count("\n") + (1 if new and not new.endswith("\n") else 0)
                        removed = old.count("\n") + (1 if old and not old.endswith("\n") else 0)
                        if not old and new:  # write_file
                            removed = 0
                        # Полный old/new — для журнала изменений (core.changes);
                        # обрезка для events.payload — в handlers.messages.
                        state["edits"].append({
                            "tool": name,
                            "file": (params.get("file_path") or params.get("path")
                                     or params.get("absolute_path") or "?"),
                            "added": added,
                            "removed": removed,
                            "old": old,
                            "new": new,
                        })
                    await emit_progress("tool_use")
                    continue

                # ---------- tool_result ----------
                if etype == "tool_result":
                    state["current_tool"] = None
                    continue

                # ---------- финал ----------
                if etype == "result":
                    if not state["text"]:
                        state["text"] = (evt.get("content") or evt.get("text")
                                          or evt.get("response") or "")
                    stats = evt.get("stats") or {}
                    if stats:
                        state["meta"]["tokens_in"] = (stats.get("input_tokens")
                                                       or stats.get("input"))
                        state["meta"]["tokens_out"] = stats.get("output_tokens")
                    continue

        try:
            await asyncio.gather(read_stdout(), read_stderr())
        except asyncio.CancelledError:
            # Родитель отменил задачу — убиваем subprocess немедленно
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                log.info("cancel: killed gemini subprocess pid=%s", proc.pid)
            raise
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await proc.wait()
                    except Exception:
                        pass

        err_text = "".join(stderr_buf)
        log.info("exec gemini stream done | rc=%s | text_chars=%d | err_chars=%d | events=%s",
                 proc.returncode, len(state["text"]), len(err_text), events_seen)

        if proc.returncode and proc.returncode != 0:
            raise RuntimeError(f"gemini failed (rc={proc.returncode}): {err_text[:2000]}")

        state["meta"]["edits"] = state["edits"]
        state["meta"]["tool_uses"] = state["tool_uses"]
        state["meta"]["tool_call_log"] = state["tool_call_log"]

        # session_id пока не возвращаем (см. комментарий в run() — Gemini-сессии
        # на этой итерации не подключаем). История идёт через build_prompt_with_history.
        return state["text"] or "(пустой ответ)", None, state["meta"]
