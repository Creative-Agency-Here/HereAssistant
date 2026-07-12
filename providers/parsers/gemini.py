"""I/O-free parser событий Gemini CLI stream-json."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from providers.models import FileEdit, ProgressMeta, ProviderMeta, ProviderResult

ToolDescription = Callable[[str, dict[str, Any]], str]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(slots=True)
class GeminiStreamParser:
    tool_description: ToolDescription
    session_id: str | None = None
    text: str = ""
    current_tool: str | None = None
    edits: list[FileEdit] = field(default_factory=list)
    tool_uses: list[str] = field(default_factory=list)
    tool_call_log: list[str] = field(default_factory=list)
    events_seen: dict[str, int] = field(default_factory=dict)
    _meta: ProviderMeta = field(default_factory=ProviderMeta)
    _tool_call_idx: dict[str, int] = field(default_factory=dict)

    def consume(self, event: Mapping[str, Any]) -> list[str]:
        evt = dict(event)
        event_type = _string(evt.get("type"), "unknown")
        self.events_seen[event_type] = self.events_seen.get(event_type, 0) + 1

        if event_type == "init":
            self.session_id = _string(evt.get("session_id")) or self.session_id
            return []

        if event_type == "message":
            if evt.get("role") != "assistant":
                return []
            content = _string(evt.get("content"))
            if evt.get("delta"):
                self.text += content
            else:
                self.text = content
            return ["partial_delta"]

        if event_type == "tool_use":
            self._consume_tool_use(evt)
            return ["tool_use"]

        if event_type == "tool_result":
            self.current_tool = None
            return []

        if event_type == "result":
            if not self.text:
                self.text = _string(evt.get("content") or evt.get("text") or evt.get("response"))
            stats = _mapping(evt.get("stats"))
            tokens_in = _integer(stats.get("input_tokens") or stats.get("input"))
            tokens_out = _integer(stats.get("output_tokens"))
            if tokens_in is not None:
                self._meta["tokens_in"] = tokens_in
            if tokens_out is not None:
                self._meta["tokens_out"] = tokens_out
            return []

        return []

    def progress_meta(self) -> ProgressMeta:
        return ProgressMeta(
            edits=[edit.to_dict() for edit in self.edits],
            tool_uses=list(self.tool_uses),
            tool_call_log=list(self.tool_call_log),
            steps=[],
            thinking="",
            current_tool=self.current_tool,
        )

    def provider_result(self) -> ProviderResult:
        meta = ProviderMeta(self._meta)
        meta["edits"] = [edit.to_dict() for edit in self.edits]
        meta["tool_uses"] = list(self.tool_uses)
        meta["tool_call_log"] = list(self.tool_call_log)
        # Gemini resume пока не включён: внешний контракт намеренно возвращает None.
        return ProviderResult(self.text or "(пустой ответ)", None, meta)

    def _consume_tool_use(self, event: dict[str, Any]) -> None:
        name = _string(event.get("tool_name") or event.get("name"), "?")
        tool_id = self._tool_id(event.get("tool_id") or event.get("id"))
        parameters = _mapping(event.get("parameters") or event.get("input"))
        self.current_tool = name
        self.tool_uses.append(name)
        self._record_tool_call(name, parameters, tool_id)
        self._record_edit(name, parameters)

    def _record_tool_call(self, name: str, parameters: dict[str, Any], tool_id: str | None) -> None:
        description = self.tool_description(name, parameters)
        if tool_id and tool_id in self._tool_call_idx:
            self.tool_call_log[self._tool_call_idx[tool_id]] = description
            return
        self.tool_call_log.append(description)
        if tool_id:
            self._tool_call_idx[tool_id] = len(self.tool_call_log) - 1

    def _record_edit(self, name: str, parameters: dict[str, Any]) -> None:
        if name not in ("write_file", "edit_file", "replace"):
            return
        old = _string(
            parameters.get("old_string") or parameters.get("old_str") or parameters.get("oldText")
        )
        new = _string(
            parameters.get("new_string")
            or parameters.get("new_str")
            or parameters.get("newText")
            or parameters.get("content")
        )
        added = new.count("\n") + (1 if new and not new.endswith("\n") else 0)
        removed = old.count("\n") + (1 if old and not old.endswith("\n") else 0)
        if not old and new:
            removed = 0
        file_name = _string(
            parameters.get("file_path")
            or parameters.get("path")
            or parameters.get("absolute_path"),
            "?",
        )
        self.edits.append(FileEdit(name, file_name, added, removed, old, new))

    @staticmethod
    def _tool_id(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
