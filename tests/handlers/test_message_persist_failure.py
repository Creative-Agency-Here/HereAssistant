"""Сбой записи в БД после работы агента не превращается в провал запроса.

Агент к этому моменту уже изменил файлы проекта. Если показать человеку ошибку,
он повторит запрос — и агент пройдёт по проекту второй раз, необратимо.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from handlers import messages


class _Policy:
    """Политика, разрешающая хранить содержимое сообщений."""


@pytest.fixture(autouse=True)
def allow_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messages.project_config, "can_store_messages", lambda _policy: True)


def test_answer_is_not_lost_when_history_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_save(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(messages.repo, "save_message", failing_save)
    conv: dict[str, Any] = {"id": 1, "model": "opus", "provider_session_id": None}

    messages._persist_answer(conv, "ответ", account={"provider": "claude_code"}, policy=_Policy())


def test_session_id_write_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_update(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(messages.repo, "update_conv", failing_update)
    conv: dict[str, Any] = {"id": 1, "provider_session_id": "old"}

    messages._persist_session_id(conv, "new-session")


def test_session_id_is_written_when_it_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str]] = []

    def capture(conv_id: int, **kwargs: object) -> None:
        calls.append((conv_id, str(kwargs.get("provider_session_id"))))

    monkeypatch.setattr(messages.repo, "update_conv", capture)
    conv: dict[str, Any] = {"id": 7, "provider_session_id": "old"}

    messages._persist_session_id(conv, "new-session")
    messages._persist_session_id(conv, "old")
    messages._persist_session_id(conv, None)

    assert calls == [(7, "new-session")], "пишем только при реальной смене сессии"


def test_history_is_skipped_when_policy_forbids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messages.project_config, "can_store_messages", lambda _policy: False)
    called = False

    def save(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(messages.repo, "save_message", save)

    messages._persist_answer({"id": 1, "model": "m"}, "ответ", account={}, policy=_Policy())

    assert not called, "privacy-гейт обязан остаться на месте"
