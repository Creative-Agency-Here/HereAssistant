"""Локальное хранилище /rc: receipts (идемпотентность), публикации, outbox, схема БД."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from core import config, db, project_config
from core.remote_control import outbox, publications, receipts


@pytest.fixture
def rc_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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


def test_rc_tables_are_created_and_hold_no_secrets(rc_database: Path) -> None:
    with sqlite3.connect(rc_database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        receipt_columns = {row[1] for row in connection.execute("PRAGMA table_info(rc_command_receipts)")}
        publication_columns = {row[1] for row in connection.execute("PRAGMA table_info(rc_publications)")}

    assert {"rc_publications", "rc_command_receipts", "rc_event_outbox"} <= tables
    # В receipts нет места под prompt/содержимое — только hash.
    assert "payload_hash" in receipt_columns
    assert not {"prompt", "content", "payload", "result"}.intersection(receipt_columns)
    # В публикациях нет cwd/project/transcript.
    assert not {"cwd", "project_name", "transcript", "prompt"}.intersection(publication_columns)


def test_receipt_command_type_is_constrained(rc_database: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with db.conn() as connection:
            connection.execute(
                """INSERT INTO rc_command_receipts
                   (command_id, sequence, command_type, payload_hash, state, claimed_at, updated_at)
                   VALUES ('c', 1, 'shell', 'h', 'claimed', 1, 1)"""
            )


# --- Негативный тест №3: повторная доставка того же command id не даёт 2-го исполнения ---


def test_duplicate_command_delivery_does_not_execute_twice(rc_database: Path) -> None:
    payload_hash = hashlib.sha256(b"prompt-body").hexdigest()
    executions = 0

    def deliver(command_id: str) -> None:
        nonlocal executions
        result = receipts.claim(
            command_id, sequence=7, command_type="prompt", payload_hash=payload_hash
        )
        if result.should_execute:
            executions += 1

    # Первая доставка — исполняем.
    deliver("command-1")
    # Повторная доставка той же команды (reconnect/at-least-once) — НЕ исполняем.
    deliver("command-1")
    deliver("command-1")

    assert executions == 1

    second = receipts.claim(
        "command-1", sequence=7, command_type="prompt", payload_hash=payload_hash
    )
    assert second.should_execute is False
    assert second.reason == "duplicate"

    with db.conn() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM rc_command_receipts WHERE command_id='command-1'"
        ).fetchone()[0]
    assert count == 1


def test_payload_hash_mismatch_fails_closed(rc_database: Path) -> None:
    first = receipts.claim("command-2", sequence=1, command_type="prompt", payload_hash="aaa")
    assert first.should_execute is True

    tampered = receipts.claim("command-2", sequence=1, command_type="prompt", payload_hash="bbb")
    assert tampered.should_execute is False
    assert tampered.reason == "payload_hash_mismatch"

    stored = receipts.get("command-2")
    assert stored is not None
    assert stored["state"] == "rejected"


def test_unknown_command_type_is_rejected(rc_database: Path) -> None:
    for bad_type in ("shell", "write_file", "approve_tool", ""):
        result = receipts.claim(f"cmd-{bad_type}", sequence=1, command_type=bad_type, payload_hash="h")
        assert result.should_execute is False
        assert result.reason == "unknown_command_type"

    with db.conn() as connection:
        count = connection.execute("SELECT COUNT(*) FROM rc_command_receipts").fetchone()[0]
    assert count == 0


def test_receipt_lifecycle_running_then_finished(rc_database: Path) -> None:
    receipts.claim("command-3", sequence=3, command_type="git_commit", payload_hash="h")
    receipts.mark_running("command-3")
    assert receipts.get("command-3")["state"] == "running"

    receipts.finish("command-3", state="succeeded", result_hash="r-hash")
    stored = receipts.get("command-3")
    assert stored["state"] == "succeeded"
    assert stored["result_hash"] == "r-hash"
    assert stored["finished_at"] is not None


def test_publish_is_gated_by_policy_and_stores_no_private_data(rc_database: Path) -> None:
    private = project_config.ProjectPolicy(mode="private")
    assert publications.publish("sess-1", policy=private, device_id="d") is None
    assert publications.get("sess-1") is None

    allowed = project_config.ProjectPolicy(
        mode="private", rc_enabled=True, rc_allow_presence_in_private=True
    )
    record = publications.publish("sess-1", policy=allowed, device_id="device-1")
    assert record is not None
    assert record["state"] == "published_idle"
    assert record["privacy_mode"] == "private"
    assert record["capabilities"]["remotePrompt"] is False


def test_republish_increments_generation_and_resets_sequence(rc_database: Path) -> None:
    allowed = project_config.ProjectPolicy(
        mode="private", rc_enabled=True, rc_allow_presence_in_private=True
    )
    publications.publish("sess-2", policy=allowed, device_id="d")
    publications.advance_sequence("sess-2", 5)
    assert publications.get("sess-2")["last_sequence"] == 5

    second = publications.publish("sess-2", policy=allowed, device_id="d")
    assert second["generation"] == 2
    assert second["last_sequence"] == 0


def test_publication_state_machine_validates_states(rc_database: Path) -> None:
    allowed = project_config.ProjectPolicy(
        mode="private", rc_enabled=True, rc_allow_presence_in_private=True
    )
    publications.publish("sess-3", policy=allowed, device_id="d")

    publications.set_state("sess-3", "running")
    assert publications.get("sess-3")["state"] == "running"

    with pytest.raises(ValueError):
        publications.set_state("sess-3", "bogus_state")

    publications.close("sess-3")
    closed = publications.get("sess-3")
    assert closed["state"] == "closed"
    assert closed["closed_at"] is not None


def test_outbox_is_idempotent_and_retryable(rc_database: Path) -> None:
    event_id = "00000000-0000-4000-8000-0000000000aa"
    assert outbox.enqueue({"status": "running"}, command_id="c1", event_id=event_id) == event_id
    # Повтор с тем же event_id — идемпотентно, одна строка.
    assert outbox.enqueue({"status": "running"}, command_id="c1", event_id=event_id) == event_id
    assert outbox.pending_count() == 1

    due = outbox.next_due()
    assert due is not None and due["event_id"] == event_id

    outbox.mark_retry(event_id, due["attempts"], "http:500")
    # После retry событие не готово к немедленной отправке.
    assert outbox.next_due() is None

    outbox.mark_delivered(event_id)
    assert outbox.pending_count() == 0
