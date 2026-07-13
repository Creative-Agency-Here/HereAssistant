import sqlite3
from pathlib import Path

import pytest

import chat
from core import config


def test_terminal_chat_lists_only_owned_or_shared_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "bridge.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE accounts (
               id INTEGER PRIMARY KEY, label TEXT, provider TEXT, enabled INTEGER,
               owner_user_id INTEGER, shared INTEGER, default_model TEXT,
               cli_home_path TEXT)"""
        )
        connection.executemany(
            """INSERT INTO accounts
               (id,label,provider,enabled,owner_user_id,shared,default_model,cli_home_path)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (1, "own", "claude_code", 1, 100, 0, None, "/own"),
                (2, "foreign", "codex", 1, 200, 0, None, "/foreign"),
                (3, "shared", "claude_code", 1, None, 1, None, "/shared"),
                (4, "disabled", "claude_code", 0, 100, 0, None, "/disabled"),
            ],
        )
    monkeypatch.setattr(config, "DB_PATH", database)

    assert [row["label"] for row in chat._db_accounts(100)] == ["own", "shared"]
    assert [row["label"] for row in chat._db_accounts(200)] == ["foreign", "shared"]
