"""Типизированные контракты между CLI-провайдерами и потребителями."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Awaitable, Protocol, TypedDict


class ToolStepDict(TypedDict):
    id: str | None
    name: str
    desc: str
    status: str
    result: str | None


class FileEditDict(TypedDict):
    tool: str
    file: str
    added: int
    removed: int
    old: str
    new: str


class ProgressMeta(TypedDict):
    edits: list[FileEditDict]
    tool_uses: list[str]
    tool_call_log: list[str]
    steps: list[ToolStepDict]
    thinking: str
    current_tool: str | None


class ProviderMeta(TypedDict, total=False):
    tokens_in: int
    tokens_out: int
    edits: list[FileEditDict]
    tool_uses: list[str]
    tool_call_log: list[str]
    steps: list[ToolStepDict]
    rate_limit_hits: int
    rate_limit_reset: object
    partial_due_to_error: bool


class ProgressCallback(Protocol):
    def __call__(self, text: str, event_type: str, meta: ProgressMeta) -> Awaitable[None]: ...


@dataclass(slots=True)
class ToolStep:
    id: str | None
    name: str
    desc: str
    status: str = "run"
    result: str | None = None

    def to_dict(self) -> ToolStepDict:
        return ToolStepDict(**asdict(self))


@dataclass(slots=True)
class FileEdit:
    tool: str
    file: str
    added: int
    removed: int
    old: str
    new: str

    def to_dict(self) -> FileEditDict:
        return FileEditDict(**asdict(self))


@dataclass(slots=True)
class ProviderResult:
    text: str
    session_id: str | None
    meta: ProviderMeta = field(default_factory=ProviderMeta)

    def as_tuple(self) -> tuple[str, str | None, ProviderMeta]:
        """Совместимость с текущими handlers/chat до их отдельной миграции."""
        return self.text, self.session_id, self.meta
