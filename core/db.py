"""SQLite-слой: схема, миграции, контекст-менеджер."""

import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username    TEXT,
    role        TEXT NOT NULL DEFAULT 'user',
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,
    label           TEXT NOT NULL UNIQUE,
    cli_home_path   TEXT NOT NULL,
    default_model   TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    chat_id              INTEGER NOT NULL,
    thread_id            INTEGER NOT NULL DEFAULT 0,
    account_id           INTEGER,
    model                TEXT,
    provider_session_id  TEXT,
    cwd                  TEXT,
    project_name         TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    UNIQUE (chat_id, thread_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    provider        TEXT,
    model           TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    user_id         INTEGER,
    chat_id         INTEGER,
    thread_id       INTEGER,
    account_label   TEXT,
    provider        TEXT,
    model           TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    duration_ms     INTEGER,
    payload         TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, timestamp);

CREATE TABLE IF NOT EXISTS file_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    thread_id   INTEGER,
    account     TEXT,
    model       TEXT,
    file        TEXT NOT NULL,
    tool        TEXT,
    added       INTEGER,
    removed     INTEGER,
    diff        TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_ts ON file_changes(ts);
CREATE INDEX IF NOT EXISTS idx_changes_file ON file_changes(file, ts);
"""

# Миграции для уже существующих БД (старые версии могут не иметь project_name и events)
MIGRATIONS = [
    # (column_check_table, column_name, alter_sql)
    ("conversations", "project_name", "ALTER TABLE conversations ADD COLUMN project_name TEXT"),
]


def _col_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def init():
    config.init_dirs()
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.executescript(SCHEMA)
        for table, col, sql in MIGRATIONS:
            if not _col_exists(conn, table, col):
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
        if config.ADMIN_ID is not None:
            conn.execute(
                "INSERT OR IGNORE INTO users(telegram_id, username, role, created_at) VALUES (?, ?, 'admin', ?)",
                (config.ADMIN_ID, None, int(time.time())),
            )
        conn.commit()


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()
