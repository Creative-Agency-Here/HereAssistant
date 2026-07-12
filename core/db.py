"""SQLite-слой: схема, миграции, контекст-менеджер."""

import sqlite3
import time
from contextlib import contextmanager

from . import config

CONVERSATIONS_TABLE_SQL = """
CREATE TABLE conversations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    chat_id              INTEGER NOT NULL,
    thread_id            INTEGER NOT NULL DEFAULT 0,
    account_id           INTEGER,
    model                TEXT,
    provider_session_id  TEXT,
    cwd                  TEXT,
    project_name         TEXT,
    project_id           INTEGER,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    UNIQUE (user_id, chat_id, thread_id)
);
"""

SCHEMA = (
    """
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
    -- NULL означает «владелец не назначен», но не даёт доступ другим пользователям.
    owner_user_id   INTEGER,
    shared          INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1))
);

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id   INTEGER NOT NULL,
    name            TEXT NOT NULL,
    root_path       TEXT NOT NULL,
    visibility      TEXT NOT NULL DEFAULT 'private'
                    CHECK (visibility IN ('private', 'shared')),
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    UNIQUE (owner_user_id, name),
    UNIQUE (root_path)
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    role            TEXT NOT NULL DEFAULT 'developer',
    created_at      INTEGER NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_user
    ON project_members(user_id, project_id);

"""
    + CONVERSATIONS_TABLE_SQL.replace(
        "CREATE TABLE conversations", "CREATE TABLE IF NOT EXISTS conversations"
    )
    + """

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
    user_id     INTEGER,
    project_id  INTEGER,
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
)

# Миграции для уже существующих БД (старые версии могут не иметь project_name и events)
MIGRATIONS = [
    # (column_check_table, column_name, alter_sql)
    ("conversations", "project_name", "ALTER TABLE conversations ADD COLUMN project_name TEXT"),
    ("conversations", "project_id", "ALTER TABLE conversations ADD COLUMN project_id INTEGER"),
    ("accounts", "owner_user_id", "ALTER TABLE accounts ADD COLUMN owner_user_id INTEGER"),
    ("file_changes", "user_id", "ALTER TABLE file_changes ADD COLUMN user_id INTEGER"),
    ("file_changes", "project_id", "ALTER TABLE file_changes ADD COLUMN project_id INTEGER"),
    (
        "accounts",
        "shared",
        "ALTER TABLE accounts ADD COLUMN shared INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1))",
    ),
    # Система доступа: существующие пользователи считаются допущенными (DEFAULT).
    ("users", "status", "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'"),
    ("users", "first_name", "ALTER TABLE users ADD COLUMN first_name TEXT"),
    ("users", "last_seen", "ALTER TABLE users ADD COLUMN last_seen INTEGER"),
    ("users", "requested_at", "ALTER TABLE users ADD COLUMN requested_at INTEGER"),
]


def _col_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _conversation_identity_is_current(conn: sqlite3.Connection) -> bool:
    """Проверяет наличие UNIQUE(user_id, chat_id, thread_id) в точном порядке."""
    for index in conn.execute("PRAGMA index_list(conversations)").fetchall():
        if not index[2]:
            continue
        columns = [row[2] for row in conn.execute(f"PRAGMA index_info({index[1]})")]
        if columns == ["user_id", "chat_id", "thread_id"]:
            return True
    return False


def _migrate_conversation_identity(conn: sqlite3.Connection) -> None:
    """Перестраивает legacy UNIQUE(chat_id, thread_id), сохраняя ID и сообщения."""
    if _conversation_identity_is_current(conn):
        return
    conn.execute("ALTER TABLE conversations RENAME TO conversations_legacy_identity")
    conn.execute(CONVERSATIONS_TABLE_SQL)
    conn.execute(
        """INSERT INTO conversations
           (id, user_id, chat_id, thread_id, account_id, model, provider_session_id,
            cwd, project_name, project_id, created_at, updated_at)
           SELECT id, user_id, chat_id, thread_id, account_id, model, provider_session_id,
                  cwd, project_name, project_id, created_at, updated_at
           FROM conversations_legacy_identity"""
    )
    conn.execute("DROP TABLE conversations_legacy_identity")


def init():
    config.init_dirs()
    with sqlite3.connect(config.DB_PATH) as conn:
        # Флаг ДО применения схемы: колонка status только добавляется сейчас?
        legacy_roles = not _col_exists(conn, "users", "status")
        conn.executescript(SCHEMA)
        # executescript завершает свою транзакцию; миграции и backfill должны
        # примениться атомарно. Ошибка DDL — startup invariant, её нельзя скрывать.
        conn.execute("BEGIN")
        for table, col, sql in MIGRATIONS:
            if not _col_exists(conn, table, col):
                conn.execute(sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_user_ts ON file_changes(user_id, ts)")
        _migrate_conversation_identity(conn)
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
