"""Claude Code provider — со стримингом, permission mode, статистикой правок."""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from core import config
from .base import CLIProvider, log, NO_WINDOW


# Колбэк прогресса: получает (накопленный_текст, event_type, meta)
ProgressCb = Callable[[str, str, dict], Awaitable[None]]


# Если переменная окружения CLAUDE_DEBUG_STREAM=1 — будем дампить
# сырые events в .runtime/logs/claude-stream-<timestamp>.jsonl для отладки.
def _maybe_dump_event(line: bytes):
    if os.environ.get("CLAUDE_DEBUG_STREAM", "").strip() not in ("1", "true", "yes"):
        return
    try:
        dump_dir = config.LOGS_DIR
        dump_dir.mkdir(parents=True, exist_ok=True)
        # один файл на запуск процесса
        if not hasattr(_maybe_dump_event, "_path"):
            _maybe_dump_event._path = dump_dir / f"claude-stream-{int(time.time())}.jsonl"
        with open(_maybe_dump_event._path, "ab") as f:
            f.write(line if line.endswith(b"\n") else line + b"\n")
    except Exception:
        pass


def _extract_text_from_block(block: dict) -> str:
    """Извлечь текст из content-блока разных форматов."""
    if not isinstance(block, dict):
        return ""
    bt = block.get("type")
    if bt == "text":
        return block.get("text", "") or ""
    # partial / delta форматы
    if bt == "text_delta":
        return block.get("text", "") or block.get("delta", "") or ""
    return ""


def _extract_text_from_message(msg: dict) -> str:
    """Из message-объекта вытащить весь текст из content."""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_extract_text_from_block(b) for b in content)
    return ""


def _short_tool_desc(name: str, inp: dict) -> str:
    """Короткое описание вызова тула: имя + ключевые аргументы."""
    if not isinstance(inp, dict):
        inp = {}

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

    if name == "Read":
        return f"Read {_name(inp.get('file_path') or inp.get('filePath'))}"
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return f"{name} {_name(inp.get('file_path') or inp.get('filePath'))}"
    if name == "Glob":
        return f"Glob {_short(inp.get('pattern'), 60)}"
    if name == "Grep":
        return f"Grep '{_short(inp.get('pattern'), 50)}'"
    if name in ("PowerShell", "Bash"):
        return f"{name}: {_short(inp.get('command'), 80)}"
    if name == "TaskCreate":
        return f"TaskCreate '{_short(inp.get('subject'), 50)}'"
    if name == "TaskUpdate":
        return f"TaskUpdate #{inp.get('taskId', '?')} → {inp.get('status', '—')}"
    if name == "TaskGet":
        return f"TaskGet #{inp.get('taskId', '?')}"
    if name == "WebFetch":
        return f"WebFetch {_short(inp.get('url'), 60)}"
    if name == "WebSearch":
        return f"WebSearch '{_short(inp.get('query'), 50)}'"
    if name == "Agent":
        return f"Agent: {_short(inp.get('description'), 50)}"
    if name == "ToolSearch":
        return f"ToolSearch {_short(inp.get('query'), 50)}"
    if name == "Skill":
        return f"Skill /{inp.get('skill', '?')}"
    if inp:
        try:
            first_val = next(iter(inp.values()), "")
            if isinstance(first_val, str) and first_val:
                return f"{name}: {_short(first_val, 60)}"
        except Exception:
            pass
    return name


class ClaudeCodeProvider(CLIProvider):
    provider_name = "claude_code"

    def env(self) -> dict:
        e = super().env()
        e["CLAUDE_CONFIG_DIR"] = str(self.cli_home)
        return e

    async def run(self, prompt, cwd, session_id, model, attachments=None,
                  progress: Optional[ProgressCb] = None):
        full_prompt = prompt or ""
        if attachments:
            paths = "\n".join(f"- {p}" for p in attachments)
            full_prompt += f"\n\n[Прикреплённые пользователем файлы — абсолютные пути]\n{paths}\n"

        perm_mode = os.environ.get("CLAUDE_PERMISSION_MODE", "acceptEdits").strip()

        argv = [
            "claude",
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode", perm_mode,
            "--append-system-prompt", config.RU_SYSTEM_INSTRUCTION,
        ]
        if model:
            argv += ["--model", model]
        if session_id:
            argv += ["--resume", session_id]

        # Промпт шлём через stdin: на Windows cmd.exe режет длинную командную
        # строку (~8191 символов) и claude падает с "The command line is too long".
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
            else:
                argv[0] = resolved

        log.info("exec %s in %s (stream)", argv[0], cwd)

        # limit=32 МБ — одна JSON-строка stream-json может быть огромной
        # (например, tool_use Read с большим файлом). Дефолт 64 КБ ронял readline.
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
            "session": session_id,
            "meta": {},                       # tokens_in, tokens_out
            "edits": [],                      # список tool_use правок (Edit/Write)
            "tool_uses": [],                  # имена всех tool_use событий
            "tool_call_log": [],              # подробное описание каждого вызова
            "tool_call_idx": {},              # tool_use_id -> индекс в tool_call_log
            "current_tool": None,
            "rate_limit_hits": 0,             # сколько раз словили rate_limit_event
            "rate_limit_reset": None,         # когда сбросится лимит (если CLI сообщил)
            "rate_limit_subtype": None,       # approaching / exceeded / warning…
            "error_subtype": None,            # subtype из result.is_error
            "error_text": None,               # текст ошибки из result-евента
        }
        stderr_buf = []
        events_seen = {}

        def _record_tool_call(name: str, inp: dict, tool_id: str | None):
            """Сохранить/обновить описание вызова тула с дедупом по id."""
            desc = _short_tool_desc(name, inp or {})
            if tool_id and tool_id in state["tool_call_idx"]:
                state["tool_call_log"][state["tool_call_idx"][tool_id]] = desc
                return
            state["tool_call_log"].append(desc)
            if tool_id:
                state["tool_call_idx"][tool_id] = len(state["tool_call_log"]) - 1

        _edit_ids: set = set()

        def _record_edit(name: str, inp: dict, tool_id: str | None):
            """Сохранить структурную правку файла (Edit/Write/...) с полным old/new —
            для журнала изменений (core.changes). Дедуп по tool_id, т.к. один tool_use
            может прийти и в assistant-событии, и отдельным сообщением."""
            if name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                return
            if not isinstance(inp, dict) or not inp:
                return
            if tool_id and tool_id in _edit_ids:
                return
            old = inp.get("old_string") or inp.get("oldString") or ""
            new = inp.get("new_string") or inp.get("newString") or inp.get("content") or ""
            added = new.count("\n") + (1 if new and not new.endswith("\n") else 0)
            removed = old.count("\n") + (1 if old and not old.endswith("\n") else 0)
            if not old and new:  # Write
                removed = 0
            state["edits"].append({
                "tool": name,
                "file": inp.get("file_path") or inp.get("filePath") or "?",
                "added": added,
                "removed": removed,
                "old": old,
                "new": new,
            })
            if tool_id:
                _edit_ids.add(tool_id)

        def _ensure_break():
            """Вставить \\n\\n перед новым смысловым блоком текста, если ещё нет."""
            t = state["text"]
            if t and not t.endswith("\n"):
                state["text"] = t + "\n\n"

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

                # ---------- системные ----------
                if etype == "system":
                    sub = evt.get("subtype")
                    if sub == "init":
                        state["session"] = evt.get("session_id") or state["session"]
                    continue

                # ---------- assistant — основной носитель текста ----------
                if etype == "assistant":
                    msg = evt.get("message", {})
                    # Вытащить tool_use-блоки с полным input — самый надёжный источник деталей
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                _record_tool_call(
                                    block.get("name") or "?",
                                    block.get("input") or {},
                                    block.get("id"),
                                )
                                _record_edit(
                                    block.get("name") or "?",
                                    block.get("input") or {},
                                    block.get("id"),
                                )
                    delta = _extract_text_from_message(msg)
                    if delta:
                        if delta.startswith(state["text"]) and len(delta) > len(state["text"]):
                            state["text"] = delta
                        elif state["text"] and delta in state["text"]:
                            pass
                        else:
                            # новый смысловой блок (модель писала между tool_use)
                            _ensure_break()
                            state["text"] += delta
                        await emit_progress("assistant_delta")
                    continue

                # ---------- stream_event — partial chunks (с --include-partial-messages) ----------
                if etype == "stream_event":
                    ev = evt.get("event", {})
                    ev_type = ev.get("type")
                    if ev_type == "content_block_delta":
                        delta = ev.get("delta", {})
                        chunk = delta.get("text", "")
                        if chunk:
                            state["text"] += chunk
                            await emit_progress("partial_delta")
                    elif ev_type == "content_block_start":
                        block = ev.get("content_block", {})
                        bt = block.get("type")
                        if bt == "tool_use":
                            name = block.get("name", "?")
                            state["current_tool"] = name
                            state["tool_uses"].append(name)
                            _record_tool_call(name, block.get("input") or {}, block.get("id"))
                            await emit_progress("tool_start")
                        elif bt == "text":
                            # модель начала новый текстовый блок между тулзами —
                            # отделяем переносом, иначе фразы слипаются
                            _ensure_break()
                    elif ev_type == "content_block_stop":
                        if state["current_tool"]:
                            state["current_tool"] = None
                    continue

                # ---------- tool_use (отдельным сообщением, не внутри stream_event) ----------
                if etype == "tool_use" or evt.get("tool_name") or evt.get("name"):
                    name = (evt.get("name") or evt.get("tool_name")
                            or evt.get("tool", {}).get("name", "?"))
                    state["current_tool"] = name
                    state["tool_uses"].append(name)
                    inp = evt.get("input") or evt.get("tool_input") or {}
                    _record_tool_call(name, inp if isinstance(inp, dict) else {},
                                       evt.get("id") or evt.get("tool_use_id"))
                    _record_edit(name, inp if isinstance(inp, dict) else {},
                                 evt.get("id") or evt.get("tool_use_id"))
                    await emit_progress("tool_use")
                    continue

                # ---------- tool_result ----------
                if etype == "tool_result":
                    state["current_tool"] = None
                    continue

                # ---------- rate limit ----------
                if etype == "rate_limit_event":
                    state["rate_limit_hits"] += 1
                    rl = evt.get("rate_limit") or evt.get("usage") or {}
                    if isinstance(rl, dict):
                        reset = (rl.get("resets_at") or rl.get("reset_at")
                                 or evt.get("resets_at") or evt.get("reset_at"))
                        if reset:
                            state["rate_limit_reset"] = reset
                        sub = evt.get("subtype") or rl.get("type") or evt.get("status")
                        if sub:
                            state["rate_limit_subtype"] = str(sub)
                    continue

                # ---------- финал ----------
                if etype == "result":
                    # Claude Code сообщает об ошибке через result.is_error + subtype,
                    # а не через stderr — поэтому сохраняем сюда, чтобы потом дать
                    # пользователю человеческое объяснение вместо сухого rc=1.
                    if evt.get("is_error"):
                        state["error_subtype"] = evt.get("subtype") or "error"
                        err_in_result = evt.get("result") or evt.get("error") or ""
                        if isinstance(err_in_result, str) and err_in_result.strip():
                            state["error_text"] = err_in_result.strip()[:500]
                    else:
                        if not state["text"]:
                            state["text"] = evt.get("result") or evt.get("text") or ""
                    state["session"] = evt.get("session_id") or state["session"]
                    usage = evt.get("usage") or evt.get("total_usage") or {}
                    if usage:
                        state["meta"]["tokens_in"] = (usage.get("input_tokens")
                                                       or usage.get("cache_read_input_tokens"))
                        state["meta"]["tokens_out"] = usage.get("output_tokens")
                    continue

        try:
            await asyncio.gather(read_stdout(), read_stderr())
        except asyncio.CancelledError:
            # Родитель отменил задачу — убиваем subprocess немедленно,
            # иначе claude продолжит крутиться фантомом и держать квоту.
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                log.info("cancel: killed claude subprocess pid=%s", proc.pid)
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
        log.info("exec claude stream done | rc=%s | text_chars=%d | err_chars=%d | "
                 "rate_limit_hits=%s error_subtype=%s events=%s",
                 proc.returncode, len(state["text"]), len(err_text),
                 state["rate_limit_hits"], state["error_subtype"], events_seen)

        # дополним meta
        state["meta"]["edits"] = state["edits"]
        state["meta"]["tool_uses"] = state["tool_uses"]
        state["meta"]["tool_call_log"] = state["tool_call_log"]
        if state["rate_limit_hits"]:
            state["meta"]["rate_limit_hits"] = state["rate_limit_hits"]
            if state["rate_limit_reset"]:
                state["meta"]["rate_limit_reset"] = state["rate_limit_reset"]

        if proc.returncode and proc.returncode != 0:
            # Собираем человеческое описание причины. stderr у Claude Code пуст
            # в большинстве случаев — ошибки приходят через JSON-стрим:
            # либо result.is_error + subtype, либо серия rate_limit_event.
            reason = err_text.strip()
            if not reason:
                if state["error_text"]:
                    reason = state["error_text"]
                elif state["error_subtype"]:
                    reason = f"claude вернул {state['error_subtype']}"
                elif state["rate_limit_hits"]:
                    reason = (f"Anthropic rate limit ({state['rate_limit_hits']} событий)"
                              + (f", лимит сбросится в {state['rate_limit_reset']}"
                                 if state["rate_limit_reset"] else "")
                              + " — попробуйте через минуту")
                else:
                    reason = "(stderr пуст — детали в .runtime/logs/bot.log)"

            # Если уже накопили часть ответа и причина — rate limit, не падаем:
            # возвращаем то что есть с пометкой, чтобы пользователь не терял прогресс.
            if state["text"] and state["rate_limit_hits"]:
                state["text"] = (
                    state["text"]
                    + f"\n\n⏳ _Ответ оборван из-за rate limit Anthropic"
                    + (f" (лимит сбросится в {state['rate_limit_reset']})"
                       if state["rate_limit_reset"] else "")
                    + " — повторите запрос через минуту._"
                )
                state["meta"]["partial_due_to_error"] = True
                return state["text"], state["session"], state["meta"]

            raise RuntimeError(f"claude failed (rc={proc.returncode}): {reason}")

        return state["text"] or "(пустой ответ)", state["session"], state["meta"]
