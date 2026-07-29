"""Исходящая очередь /rc: доставка только после подтверждения сервера.

Событие лежит локально и покидает устройство лишь когда сервер подтвердил
приём (mark_delivered удаляет запись). Сбой доставки не теряется: растёт счётчик
попыток, а время следующей попытки отодвигается экспоненциально.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import config, db
from core.remote_control import outbox


@pytest.fixture
def rc_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Изолированная SQLite: все пути рантайма уводим во временный каталог.
    runtime = tmp_path / ".runtime"
    for name, value in {
        "RUNTIME_DIR": runtime,
        "DOWNLOADS_DIR": runtime / "downloads",
        "LOGS_DIR": runtime / "logs",
        "BACKUPS_DIR": runtime / "backups",
        "STATE_DIR": runtime / "state",
        "CLI_HOMES_DIR": runtime / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    db.init()
    return config.DB_PATH


def _read(event_id: str) -> dict:
    with db.conn() as connection:
        row = connection.execute(
            "SELECT * FROM rc_event_outbox WHERE event_id=?", (event_id,)
        ).fetchone()
    return dict(row)


def test_event_stays_queued_until_server_confirms(rc_database: Path) -> None:
    event_id = "00000000-0000-4000-8000-0000000000aa"
    assert outbox.enqueue({"status": "running"}, command_id="c1", event_id=event_id, now=1000)

    # До подтверждения событие готово к отправке и никуда не исчезает: сколько бы
    # раз ни спрашивали, оно всё ещё в очереди.
    assert outbox.pending_count() == 1
    assert outbox.next_due(now=1000)["event_id"] == event_id
    assert outbox.next_due(now=1000)["event_id"] == event_id

    # Сервер подтвердил — запись удаляется и повторно не отправляется.
    outbox.mark_delivered(event_id)
    assert outbox.pending_count() == 0
    assert outbox.next_due(now=10_000_000) is None


def test_delivery_error_grows_attempts_and_delays_retry(rc_database: Path) -> None:
    event_id = "00000000-0000-4000-8000-0000000000bb"
    outbox.enqueue({"status": "succeeded"}, command_id="c2", event_id=event_id, now=1000)

    due = outbox.next_due(now=1000)
    assert due is not None and due["attempts"] == 0

    # Первая ошибка доставки: счётчик попыток растёт, следующая попытка в будущем.
    outbox.mark_retry(event_id, due["attempts"], "http:500", now=1000)
    first = _read(event_id)
    assert first["attempts"] == 1
    assert first["next_attempt_at"] > 1000
    assert first["last_error"] == "http:500"

    # Сразу после ошибки событие не готово к немедленной повторной отправке.
    assert outbox.next_due(now=1000) is None
    # Но оно не потеряно — всё ещё в очереди.
    assert outbox.pending_count() == 1

    # Вторая ошибка отодвигает попытку ещё дальше (экспоненциальный рост).
    outbox.mark_retry(event_id, first["attempts"], "http:502", now=1000)
    second = _read(event_id)
    assert second["attempts"] == 2
    assert second["next_attempt_at"] > first["next_attempt_at"]

    # Когда время следующей попытки наступает, событие снова готово к отправке.
    assert outbox.next_due(now=second["next_attempt_at"])["event_id"] == event_id


def test_enqueue_is_idempotent_per_event_id(rc_database: Path) -> None:
    event_id = "00000000-0000-4000-8000-0000000000cc"
    assert outbox.enqueue({"status": "running"}, event_id=event_id, now=1000) == event_id
    # Повтор с тем же event_id не создаёт вторую строку.
    assert outbox.enqueue({"status": "running"}, event_id=event_id, now=1000) == event_id
    assert outbox.pending_count() == 1
