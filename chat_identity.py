"""Чистые identity helpers терминального чата."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class UserRecord(Protocol):
    def __getitem__(self, key: str) -> Any: ...


def user_display(user: UserRecord) -> str:
    username = user["username"]
    return f"@{username}" if username else str(user["telegram_id"])


def find_user(users: Sequence[UserRecord], key: str) -> UserRecord | None:
    normalized = key.lstrip("@").lower()
    for user in users:
        if str(user["telegram_id"]) == normalized:
            return user
        username = user["username"]
        if username and str(username).lower() == normalized:
            return user
    return None
