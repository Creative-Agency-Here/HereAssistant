"""Typed internal row contracts shared by handlers and future TS parity fixtures."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict


class AccountLike(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class AccountRow(TypedDict):
    id: int
    provider: str
    label: str
    cli_home_path: str
    default_model: str | None
    enabled: int
    notes: str | None
    owner_user_id: int | None
    shared: int


class ConversationRow(TypedDict):
    id: int
    user_id: int
    chat_id: int
    thread_id: int
    account_id: int | None
    model: str | None
    provider_session_id: str | None
    cwd: str | None
    project_name: str | None
    project_id: int | None
    created_at: int
    updated_at: int


class ProjectRow(TypedDict):
    id: int
    owner_user_id: int
    name: str
    root_path: str
    visibility: str
    enabled: int
    created_at: int
    updated_at: int


class AccessUserRow(TypedDict):
    telegram_id: int
    username: str | None
    role: str
    status: str
    first_name: str | None
    created_at: int
    last_seen: int | None
    requested_at: int | None


class _MessageOptional(TypedDict, total=False):
    provider: str | None
    model: str | None


class MessageRow(_MessageOptional):
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: int
