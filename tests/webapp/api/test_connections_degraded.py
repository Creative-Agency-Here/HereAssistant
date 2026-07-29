"""Главный экран Mini App не падает целиком из-за одной недоступной части."""

from __future__ import annotations

import sqlite3

import pytest

from webapp.api.routes import connections


def test_safe_returns_default_on_locked_database(caplog: pytest.LogCaptureFixture) -> None:
    """Блокировку SQLite создаёт обычная фоновая запись событий."""

    def locked() -> list[str]:
        raise sqlite3.OperationalError("database is locked")

    with caplog.at_level("WARNING"):
        result = connections._safe(locked, [], what="accounts")

    assert result == []
    assert "accounts" in caplog.text


def test_safe_passes_value_through() -> None:
    assert connections._safe(lambda: [1, 2], [], what="что-то") == [1, 2]


def test_safe_does_not_swallow_unexpected_errors() -> None:
    """Ошибка программиста обязана быть видна, а не превратиться в пустоту."""

    def broken() -> list[str]:
        raise KeyError("опечатка в ключе")

    with pytest.raises(KeyError):
        connections._safe(broken, [], what="что-то")


def test_os_error_is_handled() -> None:
    """workspace_overview ходит и в файловую систему тоже."""

    def missing() -> dict[str, str]:
        raise OSError("нет доступа")

    assert connections._safe(missing, {}, what="workspace") == {}
