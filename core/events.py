"""Структурированный лог событий в таблицу events."""

import json
import logging
import sqlite3
import time
from typing import Optional

from . import db

log_handler = logging.getLogger("bridge.events")


def log(
    event_type: str,
    *,
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    account_label: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    duration_ms: Optional[int] = None,
    payload: Optional[dict] = None,
):
    """Записать событие. Не падает при ошибке БД — только логи."""
    try:
        with db.conn() as c:
            c.execute(
                """INSERT INTO events
                   (timestamp, event_type, user_id, chat_id, thread_id, account_label,
                    provider, model, tokens_in, tokens_out, duration_ms, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(time.time()),
                    event_type,
                    user_id,
                    chat_id,
                    thread_id,
                    account_label,
                    provider,
                    model,
                    tokens_in,
                    tokens_out,
                    duration_ms,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                ),
            )
    except (sqlite3.Error, TypeError, ValueError) as error:
        # Audit storage не ломает основной поток, но сбой больше не скрывается.
        log_handler.warning("event insert failed (%s)", type(error).__name__)


def stats_window(seconds: int) -> dict:
    """Сводка событий за последние N секунд."""
    cutoff = int(time.time()) - seconds
    with db.conn() as c:
        total = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE timestamp >= ? AND event_type IN ('message_in','message_out')",
            (cutoff,),
        ).fetchone()["n"]
        by_model = list(
            c.execute(
                """SELECT model, provider, account_label,
                      COUNT(*) AS msgs,
                      COALESCE(SUM(tokens_in),0) AS t_in,
                      COALESCE(SUM(tokens_out),0) AS t_out,
                      COALESCE(AVG(duration_ms),0) AS avg_ms
               FROM events
               WHERE timestamp >= ? AND event_type='message_out'
               GROUP BY model, provider, account_label
               ORDER BY msgs DESC""",
                (cutoff,),
            )
        )
        errors = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE timestamp >= ? AND event_type='error'",
            (cutoff,),
        ).fetchone()["n"]
    return {"total_messages": total, "by_model": [dict(r) for r in by_model], "errors": errors}


def recent(limit: int = 20, only_errors: bool = False, hours: int = 24):
    """Последние события."""
    cutoff = int(time.time()) - hours * 3600
    where = "WHERE timestamp >= ?"
    args = [cutoff]
    if only_errors:
        where += " AND event_type='error'"
    with db.conn() as c:
        return list(
            c.execute(
                f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?",
                (*args, limit),
            )
        )
