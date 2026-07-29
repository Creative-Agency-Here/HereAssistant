"""Идемпотентность исполнения удалённых команд и поведение при обрыве.

Receipt фиксируется в SQLite ДО запуска исполнения; сам факт его наличия —
защита от повторного исполнения при at-least-once доставке. Обрыв исполнения не
превращает receipt в успех и не запускает необратимую команду заново.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core import config, db
from core.remote_control import receipts


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


def test_receipt_is_written_before_execution_and_blocks_repeat(rc_database: Path) -> None:
    payload_hash = hashlib.sha256(b"prompt-body").hexdigest()
    executions = 0

    def deliver() -> receipts.ClaimResult:
        nonlocal executions
        result = receipts.claim(
            "command-1", sequence=7, command_type="prompt", payload_hash=payload_hash
        )
        if result.should_execute:
            # Исполнение началось бы здесь — но receipt уже обязан быть в БД.
            executions += 1
        return result

    first = deliver()
    assert first.should_execute is True

    # Receipt зафиксирован ДО исполнения: finish() ещё не вызывали, а запись уже
    # существует и помечена как claimed.
    stored = receipts.get("command-1")
    assert stored is not None
    assert stored["state"] == "claimed"
    assert stored["finished_at"] is None

    # Повторная доставка той же команды (reconnect / at-least-once) — не исполняем:
    # существующий receipt распознаётся как дубль.
    second = deliver()
    third = deliver()
    assert second.should_execute is False
    assert second.reason == "duplicate"
    assert third.should_execute is False

    # Исполнение ровно одно; в БД одна строка receipt.
    assert executions == 1
    with db.conn() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM rc_command_receipts WHERE command_id='command-1'"
        ).fetchone()[0]
    assert count == 1


def test_interrupted_receipt_does_not_become_success_or_rerun(rc_database: Path) -> None:
    payload_hash = hashlib.sha256(b"git-push-body").hexdigest()
    executions = 0

    def deliver() -> receipts.ClaimResult:
        nonlocal executions
        result = receipts.claim(
            "command-crash", sequence=3, command_type="git_push", payload_hash=payload_hash
        )
        if result.should_execute:
            executions += 1
            receipts.mark_running("command-crash")
            # Здесь процесс падает: finish() не вызывается, итог неизвестен.
        return result

    deliver()

    # Обрыв: состояние осталось неопределённым (running), успеха нет.
    interrupted = receipts.get("command-crash")
    assert interrupted["state"] == "running"
    assert interrupted["finished_at"] is None

    # Повторная доставка после рестарта: receipt уже есть, поэтому необратимая
    # команда (git push) НЕ исполняется второй раз автоматически.
    redelivered = deliver()
    assert redelivered.should_execute is False
    assert redelivered.reason == "duplicate"

    # Статус так и не превратился в succeeded сам по себе.
    after = receipts.get("command-crash")
    assert after["state"] != "succeeded"
    assert after["state"] == "running"
    assert executions == 1


def test_finish_rejects_non_terminal_state(rc_database: Path) -> None:
    # finish() принимает только терминальные состояния: «успех» нельзя записать
    # случайным промежуточным значением.
    receipts.claim("command-x", sequence=1, command_type="stop", payload_hash="h")
    with pytest.raises(ValueError):
        receipts.finish("command-x", state="running")
    with pytest.raises(ValueError):
        receipts.finish("command-x", state="claimed")


def test_explicit_terminal_outcome_is_recorded(rc_database: Path) -> None:
    # Явное завершение с ошибкой — это не «автоуспех»: состояние фиксируется как
    # есть вместе с безопасным hash результата.
    receipts.claim("command-f", sequence=2, command_type="git_commit", payload_hash="h")
    receipts.mark_running("command-f")
    receipts.finish("command-f", state="failed", result_hash="r-hash")

    stored = receipts.get("command-f")
    assert stored["state"] == "failed"
    assert stored["result_hash"] == "r-hash"
    assert stored["finished_at"] is not None


# ---------- отображение локального состояния в статус сервера ----------


def test_rejected_is_reported_to_server_as_failed() -> None:
    """``rejected`` серверу отправить нельзя — он не входит в контракт статусов.

    Отправка неизвестного значения дала бы 400: статус не сохранился бы вовсе, а
    команда осталась бы ``claimed`` до самого таймаута отправителя. Смысл отказа
    несёт код причины, а не выдуманный статус.
    """
    assert "rejected" not in receipts.SERVER_STATUSES
    assert receipts.server_status("rejected") == "failed"


def test_known_states_map_to_themselves() -> None:
    for state in ("running", "succeeded", "failed", "cancelled", "indeterminate"):
        assert receipts.server_status(state) == state
        assert state in receipts.SERVER_STATUSES


def test_unknown_state_degrades_to_failed() -> None:
    # Молча отправить невалидное значение хуже, чем честно закрыть ошибкой.
    assert receipts.server_status("нечто") == "failed"
