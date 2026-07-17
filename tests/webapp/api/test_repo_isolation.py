import json
import time
from pathlib import Path

import pytest

from core import changes, config, db
from webapp.api import repo


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", None)
    db.init()


def _event(user_id: int, kind: str, payload: dict | None = None) -> None:
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO events
               (timestamp, event_type, user_id, chat_id, thread_id, payload)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (int(time.time()), kind, user_id, user_id * 10, 0, json.dumps(payload or {})),
        )


def test_now_queries_are_scoped_to_authenticated_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _event(100, "message_in", {"text_preview": "private user 100"})
    _event(200, "message_out", {"tool_call_log": ["private user 200"]})

    task = repo.get_active_task(100)

    assert task is not None
    assert task["request_preview"] == "private user 100"
    assert repo.get_active_task(200) is None
    assert repo.get_recent_actions(100) == []
    assert repo.get_recent_actions(200) == ["private user 200"]


def test_file_changes_are_written_and_read_per_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    changes.record_edits(
        user_id=100,
        project_id=10,
        thread_id=1,
        account="claude-a",
        model="model-a",
        edits=[{"file": "a.py", "tool": "Edit", "old": "a", "new": "b"}],
    )
    changes.record_edits(
        user_id=200,
        project_id=20,
        thread_id=2,
        account="claude-b",
        model="model-b",
        edits=[{"file": "b.py", "tool": "Edit", "old": "x", "new": "y"}],
    )

    first = repo.list_file_changes(100)
    second = repo.list_file_changes(200)

    assert [(item["file"], item["project_id"]) for item in first] == [("a.py", 10)]
    assert [(item["file"], item["project_id"]) for item in second] == [("b.py", 20)]


def test_cli_connection_view_returns_only_safe_owned_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    with db.conn() as connection:
        connection.executemany(
            """INSERT INTO accounts
               (provider, label, cli_home_path, default_model, enabled, owner_user_id, shared)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            [
                ("claude_code", "own", "/secret/own", "opus", 100, 0),
                ("codex", "foreign", "/secret/foreign", "codex", 200, 0),
                ("gemini", "shared", "/secret/shared", None, None, 1),
            ],
        )

    accounts = repo.list_cli_accounts(100)

    assert [item["label"] for item in accounts] == ["own", "shared"]
    assert all(
        set(item) == {"provider", "label", "defaultModel", "shared"}
        for item in accounts
    )
