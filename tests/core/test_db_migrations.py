import sqlite3
from pathlib import Path

import pytest

from core import config, db


def configure_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    monkeypatch.setattr(config, "ADMIN_IDS", [100])
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    return config.DB_PATH


def columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                label TEXT NOT NULL UNIQUE,
                cli_home_path TEXT NOT NULL,
                default_model TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                notes TEXT
            );
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL DEFAULT 0,
                account_id INTEGER,
                model TEXT,
                provider_session_id TEXT,
                cwd TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE (chat_id, thread_id)
            );
            INSERT INTO users(telegram_id, username, role, created_at)
            VALUES (100, 'owner', 'admin', 1), (200, 'old-admin', 'admin', 1);
            INSERT INTO accounts(provider, label, cli_home_path)
            VALUES ('claude_code', 'legacy', '/tmp/legacy-home');
            INSERT INTO conversations(
                user_id, chat_id, thread_id, account_id, cwd, created_at, updated_at
            ) VALUES (100, 10, 0, 1, '/tmp/project', 1, 1);
            """
        )


def test_fresh_database_init_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = configure_database(tmp_path, monkeypatch)

    db.init()
    db.init()

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        owner = connection.execute(
            "SELECT role, status FROM users WHERE telegram_id=100"
        ).fetchone()

    assert {
        "users",
        "settings",
        "accounts",
        "projects",
        "project_members",
        "conversations",
        "messages",
        "events",
        "tasks",
        "file_changes",
    } <= tables
    assert owner == ("admin", "approved")


def test_legacy_database_is_migrated_without_data_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = configure_database(tmp_path, monkeypatch)
    create_legacy_database(database_path)

    db.init()

    with sqlite3.connect(database_path) as connection:
        users = connection.execute(
            "SELECT telegram_id, username, role, status FROM users ORDER BY telegram_id"
        ).fetchall()
        account = connection.execute(
            "SELECT provider, label, cli_home_path, owner_user_id, shared FROM accounts"
        ).fetchone()
        conversation = connection.execute(
            "SELECT chat_id, cwd, project_name, project_id FROM conversations"
        ).fetchone()
        user_columns = columns(connection, "users")

    assert users == [
        (100, "owner", "admin", "approved"),
        (200, "old-admin", "user", "approved"),
    ]
    assert account == ("claude_code", "legacy", "/tmp/legacy-home", None, 0)
    assert conversation == (10, "/tmp/project", None, None)
    assert {"status", "first_name", "last_seen", "requested_at"} <= user_columns

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO conversations
               (user_id,chat_id,thread_id,created_at,updated_at)
               VALUES (200,10,0,2,2)"""
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM conversations WHERE chat_id=10 AND thread_id=0"
            ).fetchone()[0]
            == 2
        )


def test_migrated_database_remains_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = configure_database(tmp_path, monkeypatch)
    create_legacy_database(database_path)

    db.init()
    db.init()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1


def test_failed_migration_rolls_back_all_migration_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = configure_database(tmp_path, monkeypatch)
    create_legacy_database(database_path)
    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [
            (
                "accounts",
                "temporary_column",
                "ALTER TABLE accounts ADD COLUMN temporary_column TEXT",
            ),
            (
                "accounts",
                "broken_column",
                "ALTER TABLE missing_table ADD COLUMN broken_column TEXT",
            ),
        ],
    )

    with pytest.raises(sqlite3.OperationalError, match="missing_table"):
        db.init()

    with sqlite3.connect(database_path) as connection:
        assert "temporary_column" not in columns(connection, "accounts")
