"""Общие операции с БД, используемые хендлерами."""

import sqlite3
import time
from typing import Optional

from core import db, config


def get_or_create_conv(chat_id: int, thread_id: int, user_id: int) -> sqlite3.Row:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM conversations WHERE chat_id=? AND thread_id=?",
            (chat_id, thread_id),
        ).fetchone()
        if row:
            return row
        now = int(time.time())
        default_account = c.execute(
            "SELECT id, default_model FROM accounts WHERE enabled=1 ORDER BY id LIMIT 1"
        ).fetchone()
        acc_id = default_account["id"] if default_account else None
        model = default_account["default_model"] if default_account else None
        c.execute(
            """INSERT INTO conversations
               (user_id, chat_id, thread_id, account_id, model, cwd, project_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, chat_id, thread_id, acc_id, model, config.DEFAULT_CWD, "default", now, now),
        )
        return c.execute(
            "SELECT * FROM conversations WHERE chat_id=? AND thread_id=?",
            (chat_id, thread_id),
        ).fetchone()


def update_conv(conv_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.conn() as c:
        c.execute(
            f"UPDATE conversations SET {cols}, updated_at=? WHERE id=?",
            (*fields.values(), int(time.time()), conv_id),
        )


def load_history(conv_id: int, limit: int = None):
    limit = limit or config.MAX_HISTORY
    with db.conn() as c:
        return list(c.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conv_id, limit),
        ))[::-1]


def save_message(conv_id: int, role: str, content: str,
                 provider: Optional[str] = None, model: Optional[str] = None):
    with db.conn() as c:
        c.execute(
            """INSERT INTO messages (conversation_id, role, content, provider, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conv_id, role, content, provider, model, int(time.time())),
        )
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                  (int(time.time()), conv_id))


def list_accounts():
    with db.conn() as c:
        return list(c.execute("SELECT * FROM accounts WHERE enabled=1 ORDER BY id"))


def get_account(account_id: int):
    with db.conn() as c:
        return c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()


def get_account_by_label(label: str):
    with db.conn() as c:
        return c.execute("SELECT * FROM accounts WHERE label=? AND enabled=1", (label,)).fetchone()


def build_prompt_with_history(conv: sqlite3.Row, user_text: str) -> str:
    """При смене провайдера нативная сессия теряется — даём краткий контекст из БД."""
    history = load_history(conv["id"])
    if not history:
        return user_text
    lines = ["[Prior conversation context]"]
    for m in history[-config.MAX_HISTORY:]:
        who = "User" if m["role"] == "user" else "Assistant"
        text = m["content"]
        if len(text) > 1500:
            text = text[:1500] + "…"
        lines.append(f"{who}: {text}")
    lines.append("[End of context]\n")
    lines.append(f"User: {user_text}")
    return "\n".join(lines)
