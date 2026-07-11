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
    created_at  INTEGER NOT NULL,
    -- Доступ: approved (допущен) / pending (заявка ждёт) / denied (отклонён).
    status       TEXT NOT NULL DEFAULT 'approved',
    first_name   TEXT,
    last_seen    INTEGER,
    -- Когда владельцу отправлена карточка-заявка (защита от спама повторами).
    requested_at INTEGER
);

-- Настройки бота (живут в БД, не в .env): access_mode и будущие ключи.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,
    label           TEXT NOT NULL UNIQUE,
    cli_home_path   TEXT NOT NULL,
    default_model   TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    notes           TEXT,
    -- Владелец аккаунта (Telegram user_id). NULL = общий (доступен всем админам).
    -- Диалог пользователя берёт СВОЙ аккаунт, если есть, иначе общий.
    owner_user_id   INTEGER
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

-- Задачи сервисного API (/api/v1/tasks). Только для проектов mode: crm —
-- private/local через service API невидимы (см. core/project_config.py).
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    crm_project_id  TEXT NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new',
    meta            TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(crm_project_id, updated_at);

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
    ("accounts", "owner_user_id", "ALTER TABLE accounts ADD COLUMN owner_user_id INTEGER"),
    # Система доступа: существующие пользователи считаются допущенными (DEFAULT).
    ("users", "status", "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'"),
    ("users", "first_name", "ALTER TABLE users ADD COLUMN first_name TEXT"),
    ("users", "last_seen", "ALTER TABLE users ADD COLUMN last_seen INTEGER"),
    ("users", "requested_at", "ALTER TABLE users ADD COLUMN requested_at INTEGER"),
]


def _col_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def init():
    config.init_dirs()
    with sqlite3.connect(config.DB_PATH) as conn:
        # Флаг ДО применения схемы: колонка status только добавляется сейчас?
        legacy_roles = not _col_exists(conn, "users", "status")
        conn.executescript(SCHEMA)
        for table, col, sql in MIGRATIONS:
            if not _col_exists(conn, table, col):
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
        if legacy_roles:
            # Одноразовый бэкфилл: старый код создавал строки users ТОЛЬКО с
            # role='admin', когда роль ничего не решала. Теперь решает — иначе
            # давно отозванные владельцы молча воскресли бы полным доступом.
            # Админство остаётся только текущим владельцам из .env.
            ids = ",".join(str(int(i)) for i in config.ADMIN_IDS) or "0"
            conn.execute(f"UPDATE users SET role='user' WHERE telegram_id NOT IN ({ids})")
        if config.ADMIN_ID is not None:
            # Upsert, не IGNORE: строка владельца могла появиться раньше как
            # pending/user (middleware фиксирует пишущих до клейма) — лечим.
            conn.execute(
                "INSERT INTO users(telegram_id, username, role, status, created_at) "
                "VALUES (?, NULL, 'admin', 'approved', ?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET role='admin', status='approved'",
                (config.ADMIN_ID, int(time.time())),
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
