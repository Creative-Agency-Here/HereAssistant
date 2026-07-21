"""Stateful, но I/O-free parser событий Claude Code stream-json."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from providers.models import FileEdit, ProgressMeta, ProviderMeta, ProviderResult, ToolStep

TextFromMessage = Callable[[dict[str, Any]], str]
ThinkingFromBlock = Callable[[dict[str, Any]], str]
ResultPreview = Callable[[object], str]
ToolDescription = Callable[[str, dict[str, Any]], str]


def _string(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(slots=True)
class ClaudeStreamParser:
    text_from_message: TextFromMessage
    thinking_from_block: ThinkingFromBlock
    result_preview: ResultPreview
    tool_description: ToolDescription
    session_id: str | None = None
    text: str = ""
    thinking: str = ""
    current_tool: str | None = None
    edits: list[FileEdit] = field(default_factory=list)
    tool_uses: list[str] = field(default_factory=list)
    tool_call_log: list[str] = field(default_factory=list)
    steps: list[ToolStep] = field(default_factory=list)
    rate_limit_hits: int = 0
    rate_limit_reset: object | None = None
    rate_limit_subtype: str | None = None
    error_subtype: str | None = None
    error_text: str | None = None
    events_seen: dict[str, int] = field(default_factory=dict)
    _meta: ProviderMeta = field(default_factory=ProviderMeta)
    _tool_call_idx: dict[str, int] = field(default_factory=dict)
    _step_idx: dict[str, int] = field(default_factory=dict)
    _edit_ids: set[str] = field(default_factory=set)

    def consume(self, event: Mapping[str, Any]) -> list[str]:
        """Применяет одно JSON-событие и возвращает типы progress-обновлений."""
        evt = dict(event)
        event_type = _string(evt.get("type"), "unknown")
        self.events_seen[event_type] = self.events_seen.get(event_type, 0) + 1

        if event_type == "system":
            # Claude называет старт `init`, Qwen Code — `session_start`.
            self.session_id = _string(evt.get("session_id")) or self.session_id
            return []

        if event_type == "assistant":
            return self._consume_assistant(_mapping(evt.get("message")))

        if event_type == "stream_event":
            return self._consume_stream_event(_mapping(evt.get("event")))

        if event_type == "tool_use" or evt.get("tool_name") or evt.get("name"):
            name = _string(evt.get("name")) or _string(evt.get("tool_name"))
            if not name:
                name = _string(_mapping(evt.get("tool")).get("name"), "?")
            tool_id = self._tool_id(evt.get("id") or evt.get("tool_use_id"))
            tool_input = _mapping(evt.get("input") or evt.get("tool_input"))
            self.current_tool = name
            self.tool_uses.append(name)
            self._record_tool_call(name, tool_input, tool_id)
            self._record_edit(name, tool_input, tool_id)
            return ["tool_use"]

        if event_type == "user":
            return self._consume_user(_mapping(evt.get("message")))

        if event_type == "tool_result":
            self._set_step_result(
                self._tool_id(evt.get("tool_use_id") or evt.get("id")),
                bool(evt.get("is_error")),
                self.result_preview(evt.get("content") or evt.get("output")),
            )
            self.current_tool = None
            return ["tool_result"]

        if event_type == "rate_limit_event":
            self._consume_rate_limit(evt)
            return []

        if event_type == "result":
            self._consume_result(evt)
            return []

        return []

    def progress_meta(self) -> ProgressMeta:
        return ProgressMeta(
            edits=[edit.to_dict() for edit in self.edits],
            tool_uses=list(self.tool_uses),
            tool_call_log=list(self.tool_call_log),
            steps=[step.to_dict() for step in self.steps],
            thinking=self.thinking,
            current_tool=self.current_tool,
        )

    def provider_result(self) -> ProviderResult:
        meta = ProviderMeta(self._meta)
        meta["edits"] = [edit.to_dict() for edit in self.edits]
        meta["tool_uses"] = list(self.tool_uses)
        meta["tool_call_log"] = list(self.tool_call_log)
        meta["steps"] = [step.to_dict() for step in self.steps]
        if self.rate_limit_hits:
            meta["rate_limit_hits"] = self.rate_limit_hits
            if self.rate_limit_reset is not None:
                meta["rate_limit_reset"] = self.rate_limit_reset
        return ProviderResult(self.text or "(пустой ответ)", self.session_id, meta)

    def error_reason(self, stderr: str) -> str:
        if stderr.strip():
            return stderr.strip()
        if self.error_text:
            return self.error_text
        if self.error_subtype:
            return f"claude вернул {self.error_subtype}"
        if self.rate_limit_hits:
            reason = f"Anthropic rate limit ({self.rate_limit_hits} событий)"
            if self.rate_limit_reset is not None:
                reason += f", лимит сбросится в {self.rate_limit_reset}"
            return reason + " — попробуйте через минуту"
        return "(stderr пуст — детали в .runtime/logs/bot.log)"

    def partial_rate_limit_result(self) -> ProviderResult:
        suffix = "\n\n⏳ _Ответ оборван из-за rate limit Anthropic"
        if self.rate_limit_reset is not None:
            suffix += f" (лимит сбросится в {self.rate_limit_reset})"
        self.text += suffix + " — повторите запрос через минуту._"
        result = self.provider_result()
        result.meta["partial_due_to_error"] = True
        return result

    def _consume_assistant(self, message: dict[str, Any]) -> list[str]:
        updates: list[str] = []
        content = message.get("content")
        if isinstance(content, list):
            for raw_block in content:
                block = _mapping(raw_block)
                if not block:
                    continue
                if block.get("type") == "thinking":
                    thinking = self.thinking_from_block(block)
                    if thinking and thinking not in self.thinking:
                        self.thinking = thinking
                        updates.append("thinking")
                    continue
                if block.get("type") == "tool_use":
                    name = _string(block.get("name"), "?")
                    tool_input = _mapping(block.get("input"))
                    tool_id = self._tool_id(block.get("id"))
                    self._record_tool_call(name, tool_input, tool_id)
                    self._record_edit(name, tool_input, tool_id)

        delta = self.text_from_message(message)
        if delta:
            if delta.startswith(self.text) and len(delta) > len(self.text):
                self.text = delta
            elif self.text and delta in self.text:
                pass
            else:
                self._ensure_break()
                self.text += delta
            updates.append("assistant_delta")
        return updates

    def _consume_stream_event(self, event: dict[str, Any]) -> list[str]:
        event_type = event.get("type")
        if event_type == "content_block_delta":
            delta = _mapping(event.get("delta"))
            chunk = _string(delta.get("text"))
            if chunk:
                self.text += chunk
                return ["partial_delta"]
            if delta.get("type") == "thinking_delta" or delta.get("thinking"):
                thinking = _string(delta.get("thinking"))
                if thinking:
                    self.thinking += thinking
                    return ["thinking_delta"]
            return []

        if event_type == "content_block_start":
            block = _mapping(event.get("content_block"))
            if block.get("type") == "tool_use":
                name = _string(block.get("name"), "?")
                tool_input = _mapping(block.get("input"))
                tool_id = self._tool_id(block.get("id"))
                self.current_tool = name
                self.tool_uses.append(name)
                self._record_tool_call(name, tool_input, tool_id)
                self._record_edit(name, tool_input, tool_id)
                return ["tool_start"]
            if block.get("type") == "text":
                self._ensure_break()
            return []

        if event_type == "content_block_stop" and self.current_tool:
            self.current_tool = None
        return []

    def _consume_user(self, message: dict[str, Any]) -> list[str]:
        content = message.get("content")
        if not isinstance(content, list):
            return []
        got_result = False
        for raw_block in content:
            block = _mapping(raw_block)
            if block.get("type") != "tool_result":
                continue
            got_result = True
            self._set_step_result(
                self._tool_id(block.get("tool_use_id")),
                bool(block.get("is_error")),
                self.result_preview(block.get("content")),
            )
        if not got_result:
            return []
        self.current_tool = None
        return ["tool_result"]

    def _consume_rate_limit(self, event: dict[str, Any]) -> None:
        self.rate_limit_hits += 1
        rate_limit = _mapping(event.get("rate_limit") or event.get("usage"))
        reset = (
            rate_limit.get("resets_at")
            or rate_limit.get("reset_at")
            or event.get("resets_at")
            or event.get("reset_at")
        )
        if reset:
            self.rate_limit_reset = reset
        subtype = event.get("subtype") or rate_limit.get("type") or event.get("status")
        if subtype:
            self.rate_limit_subtype = str(subtype)

    def _consume_result(self, event: dict[str, Any]) -> None:
        if event.get("is_error"):
            self.error_subtype = _string(event.get("subtype"), "error")
            error = event.get("result") or event.get("error")
            if isinstance(error, str) and error.strip():
                self.error_text = error.strip()[:500]
            elif isinstance(error, Mapping):
                message = _string(error.get("message"))
                if message.strip():
                    self.error_text = message.strip()[:500]
        elif not self.text:
            self.text = _string(event.get("result") or event.get("text"))
        self.session_id = _string(event.get("session_id")) or self.session_id
        usage = _mapping(event.get("usage") or event.get("total_usage"))
        tokens_in = _integer(usage.get("input_tokens") or usage.get("cache_read_input_tokens"))
        tokens_out = _integer(usage.get("output_tokens"))
        if tokens_in is not None:
            self._meta["tokens_in"] = tokens_in
        if tokens_out is not None:
            self._meta["tokens_out"] = tokens_out

    def _record_tool_call(self, name: str, tool_input: dict[str, Any], tool_id: str | None) -> None:
        description = self.tool_description(name, tool_input)
        if tool_id and tool_id in self._step_idx:
            self.steps[self._step_idx[tool_id]].desc = description
        else:
            self.steps.append(ToolStep(tool_id, name, description))
            if tool_id:
                self._step_idx[tool_id] = len(self.steps) - 1

        if tool_id and tool_id in self._tool_call_idx:
            self.tool_call_log[self._tool_call_idx[tool_id]] = description
            return
        self.tool_call_log.append(description)
        if tool_id:
            self._tool_call_idx[tool_id] = len(self.tool_call_log) - 1

    def _set_step_result(self, tool_id: str | None, is_error: bool, preview: str) -> None:
        index = self._step_idx.get(tool_id) if tool_id else None
        if index is None:
            return
        step = self.steps[index]
        step.status = "err" if is_error else "ok"
        if preview:
            step.result = preview

    def _record_edit(self, name: str, tool_input: dict[str, Any], tool_id: str | None) -> None:
        if name not in (
            "Edit",
            "Write",
            "MultiEdit",
            "NotebookEdit",
            "edit",
            "edit_file",
            "write_file",
            "notebook_edit",
        ) or not tool_input:
            return
        if tool_id and tool_id in self._edit_ids:
            return
        old = _string(
            tool_input.get("old_string")
            or tool_input.get("oldString")
            or tool_input.get("oldText")
        )
        new = _string(
            tool_input.get("new_string")
            or tool_input.get("newString")
            or tool_input.get("newText")
            or tool_input.get("content")
        )
        added = new.count("\n") + (1 if new and not new.endswith("\n") else 0)
        removed = old.count("\n") + (1 if old and not old.endswith("\n") else 0)
        if not old and new:
            removed = 0
        file_name = _string(
            tool_input.get("file_path")
            or tool_input.get("filePath")
            or tool_input.get("path")
            or tool_input.get("absolute_path"),
            "?",
        )
        self.edits.append(FileEdit(name, file_name, added, removed, old, new))
        if tool_id:
            self._edit_ids.add(tool_id)

    def _ensure_break(self) -> None:
        if self.text and not self.text.endswith("\n"):
            self.text += "\n\n"

    @staticmethod
    def _tool_id(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
