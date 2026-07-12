"""Запросы к bridge.sqlite3 для веб-API.

Бот пишет в ту же БД, веб только читает (плюс редкие записи: smena аккаунта и т.п.).
SQLite сам разруливает блокировки между процессами.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from core import db


def _row(r) -> Optional[dict]:
    return dict(r) if r else None


def _parse_payload(s: Optional[str]) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}


# ---------- активная задача ----------


def get_active_task(stale_after_sec: int = 1800) -> Optional[dict]:
    """Активная задача = последний message_in без сопровождающего message_out/error.

    Если последний message_in старше stale_after_sec — считаем «зависшим» и
    возвращаем None (бот, скорее всего, упал).
    """
    cutoff = int(time.time()) - stale_after_sec
    with db.conn() as c:
        # последний message_in за окно
        row = c.execute(
            """SELECT * FROM events
               WHERE event_type='message_in' AND timestamp >= ?
               ORDER BY id DESC LIMIT 1""",
            (cutoff,),
        ).fetchone()
        if not row:
            return None
        # есть ли после него message_out / error для той же пары chat+thread?
        closed = c.execute(
            """SELECT 1 FROM events
               WHERE id > ? AND user_id=? AND chat_id=? AND thread_id=?
                 AND event_type IN ('message_out', 'error')
               LIMIT 1""",
            (row["id"], row["user_id"], row["chat_id"], row["thread_id"]),
        ).fetchone()
        if closed:
            return None

        # подтянем модель/аккаунт из последнего message_out (или из conversations)
        conv = c.execute(
            """SELECT c.model, c.cwd, c.project_name, a.label AS account_label
               FROM conversations c
               LEFT JOIN accounts a ON a.id = c.account_id
               WHERE c.user_id=? AND c.chat_id=? AND c.thread_id=?""",
            (row["user_id"], row["chat_id"], row["thread_id"]),
        ).fetchone()
        conv_d = _row(conv) or {}

    payload = _parse_payload(row["payload"])
    return {
        "active": True,
        "started_at": row["timestamp"],
        "elapsed_sec": int(time.time()) - row["timestamp"],
        "chat_id": row["chat_id"],
        "thread_id": row["thread_id"],
        "account": conv_d.get("account_label"),
        "model": conv_d.get("model"),
        "project": conv_d.get("project_name") or conv_d.get("cwd"),
        "request_preview": payload.get("text_preview", "")[:300],
    }


def get_recent_actions(limit: int = 5) -> list[str]:
    """Последние шаги ассистента из payload.tool_call_log самого свежего message_out."""
    with db.conn() as c:
        row = c.execute(
            """SELECT payload FROM events
               WHERE event_type='message_out'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return []
    payload = _parse_payload(row["payload"])
    log = payload.get("tool_call_log") or payload.get("tool_uses") or []
    return [str(x) for x in log[-limit:][::-1]]


# ---------- история диалогов ----------


def list_conversations(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    account: Optional[str] = None,
    q: Optional[str] = None,
) -> list[dict]:
    where = ["c.user_id=?"]
    args: list[Any] = [user_id]
    if account:
        where.append("a.label = ?")
        args.append(account)
    if q:
        where.append(
            "EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.content LIKE ?)"
        )
        args.append(f"%{q}%")

    sql = f"""
        SELECT c.id, c.chat_id, c.thread_id, c.model, c.project_name, c.cwd,
               c.created_at, c.updated_at,
               a.label AS account,
               (SELECT content FROM messages m
                WHERE m.conversation_id=c.id AND m.role='user'
                ORDER BY id DESC LIMIT 1) AS last_user,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS msg_count
        FROM conversations c
        LEFT JOIN accounts a ON a.id = c.account_id
        WHERE {" AND ".join(where)}
        ORDER BY c.updated_at DESC
        LIMIT ? OFFSET ?
    """
    args += [limit, offset]
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def get_conversation(conv_id: int, user_id: int) -> Optional[dict]:
    with db.conn() as c:
        conv = c.execute(
            """SELECT c.*, a.label AS account
               FROM conversations c
               LEFT JOIN accounts a ON a.id = c.account_id
               WHERE c.id = ? AND c.user_id=?""",
            (conv_id, user_id),
        ).fetchone()
        if not conv:
            return None
        msgs = list(
            c.execute(
                """SELECT id, role, content, model, provider, created_at
               FROM messages WHERE conversation_id=? ORDER BY id""",
                (conv_id,),
            )
        )
    out = dict(conv)
    out["messages"] = [dict(m) for m in msgs]
    return out


# ---------- журнал изменений файлов ----------


def list_file_changes(
    limit: int = 50,
    offset: int = 0,
    file: Optional[str] = None,
    thread_id: Optional[int] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> list[dict]:
    """Лента правок из file_changes (свежие первыми).
    Фильтры: файл, тред, окно времени [since, until] по ts — для «правок одного запроса»."""
    where = ["1=1"]
    args: list[Any] = []
    if file:
        where.append("file LIKE ?")
        args.append(f"%{file}%")
    if thread_id is not None:
        where.append("thread_id = ?")
        args.append(thread_id)
    if since is not None:
        where.append("ts >= ?")
        args.append(since)
    if until is not None:
        where.append("ts <= ?")
        args.append(until)
    sql = f"""
        SELECT id, ts, thread_id, account, model, file, tool, added, removed, diff
        FROM file_changes
        WHERE {" AND ".join(where)}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """
    args += [limit, offset]
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, args)]
