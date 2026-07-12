"""Общие операции с БД, используемые хендлерами."""

import sqlite3
import time
from pathlib import Path
from typing import Optional

from core import config, db, projects

ACCOUNT_NOT_AVAILABLE = "ACCOUNT_NOT_AVAILABLE"


def _default_account(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT id, default_model FROM accounts
           WHERE enabled=1 AND (owner_user_id=? OR shared=1)
           ORDER BY (owner_user_id=?) DESC, id LIMIT 1""",
        (user_id, user_id),
    ).fetchone()


def _normalize_conversation_project(row: sqlite3.Row, user_id: int) -> sqlite3.Row:
    """Legacy arbitrary cwd не становится доверенным проектом автоматически."""
    project_id = row["project_id"]
    if project_id and projects.get_accessible_project(user_id, project_id):
        return row

    available = projects.ensure_personal_workspace_projects(user_id)
    legacy_cwd = row["cwd"]
    legacy_name = row["project_name"]
    if legacy_cwd:
        try:
            resolved_legacy = Path(legacy_cwd).resolve(strict=True)
        except OSError:
            resolved_legacy = None
        if resolved_legacy is not None:
            for project in available:
                root = Path(project["root_path"]).resolve(strict=True)
                if legacy_name == project["name"] and (
                    resolved_legacy == root or resolved_legacy.is_relative_to(root)
                ):
                    update_conv(row["id"], project_id=project["id"], cwd=str(resolved_legacy))
                    return _conversation_by_id(row["id"])

    default = projects.ensure_default_project(user_id)
    update_conv(
        row["id"],
        project_id=default["id"],
        project_name=default["name"],
        cwd=default["root_path"],
        provider_session_id=None,
    )
    return _conversation_by_id(row["id"])


def _normalize_conversation_account(row: sqlite3.Row, user_id: int) -> sqlite3.Row:
    """Сбрасывает legacy/чужой account и выбирает только собственный либо shared."""
    with db.conn() as connection:
        current = None
        if row["account_id"]:
            current = connection.execute(
                """SELECT id FROM accounts
                   WHERE id=? AND enabled=1 AND (owner_user_id=? OR shared=1)""",
                (row["account_id"], user_id),
            ).fetchone()
        if current:
            return row
        replacement = _default_account(connection, user_id)
    update_conv(
        row["id"],
        account_id=replacement["id"] if replacement else None,
        model=replacement["default_model"] if replacement else None,
        provider_session_id=None,
    )
    return _conversation_by_id(row["id"])


def _conversation_by_id(conversation_id: int) -> sqlite3.Row:
    with db.conn() as connection:
        row = connection.execute(
            "SELECT * FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Conversation исчезла: {conversation_id}")
    return row


def get_conversation_for_user(conversation_id: int, user_id: int) -> sqlite3.Row | None:
    with db.conn() as connection:
        return connection.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user_id),
        ).fetchone()


def get_or_create_conv(chat_id: int, thread_id: int, user_id: int) -> sqlite3.Row:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM conversations WHERE user_id=? AND chat_id=? AND thread_id=?",
            (user_id, chat_id, thread_id),
        ).fetchone()
    if row:
        row = _normalize_conversation_account(row, user_id)
        return _normalize_conversation_project(row, user_id)

    default_project = projects.ensure_default_project(user_id)
    with db.conn() as c:
        now = int(time.time())
        default_account = _default_account(c, user_id)
        acc_id = default_account["id"] if default_account else None
        model = default_account["default_model"] if default_account else None
        c.execute(
            """INSERT INTO conversations
               (user_id, chat_id, thread_id, account_id, model, cwd, project_name, project_id,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                chat_id,
                thread_id,
                acc_id,
                model,
                default_project["root_path"],
                default_project["name"],
                default_project["id"],
                now,
                now,
            ),
        )
        return c.execute(
            "SELECT * FROM conversations WHERE user_id=? AND chat_id=? AND thread_id=?",
            (user_id, chat_id, thread_id),
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
        return list(
            c.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (conv_id, limit),
            )
        )[::-1]


def save_message(
    conv_id: int,
    role: str,
    content: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
):
    with db.conn() as c:
        c.execute(
            """INSERT INTO messages (conversation_id, role, content, provider, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conv_id, role, content, provider, model, int(time.time())),
        )
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (int(time.time()), conv_id))


def list_accounts(user_id: int):
    with db.conn() as c:
        return list(
            c.execute(
                """SELECT * FROM accounts
                   WHERE enabled=1 AND (owner_user_id=? OR shared=1)
                   ORDER BY (owner_user_id=?) DESC, id""",
                (user_id, user_id),
            )
        )


def get_account(account_id: int, user_id: int):
    with db.conn() as c:
        return c.execute(
            """SELECT * FROM accounts
               WHERE id=? AND enabled=1 AND (owner_user_id=? OR shared=1)""",
            (account_id, user_id),
        ).fetchone()


def get_account_by_label(label: str, user_id: int):
    with db.conn() as c:
        return c.execute(
            """SELECT * FROM accounts
               WHERE label=? AND enabled=1 AND (owner_user_id=? OR shared=1)""",
            (label, user_id),
        ).fetchone()


def build_prompt_with_history(conv: sqlite3.Row, user_text: str) -> str:
    """При смене провайдера нативная сессия теряется — даём краткий контекст из БД."""
    history = load_history(conv["id"])
    if not history:
        return user_text
    lines = ["[Prior conversation context]"]
    for m in history[-config.MAX_HISTORY :]:
        who = "User" if m["role"] == "user" else "Assistant"
        text = m["content"]
        if len(text) > 1500:
            text = text[:1500] + "…"
        lines.append(f"{who}: {text}")
    lines.append("[End of context]\n")
    lines.append(f"User: {user_text}")
    return "\n".join(lines)
