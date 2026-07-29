"""Исходящая очередь статусов/результатов /rc (rc_event_outbox).

Событие кладётся локально до отправки и покидает устройство только после
подтверждения сервера. Успешная доставка удаляет запись; сбой — экспоненциальный
retry. Payload собирается вызывающим кодом без prompt/содержимого.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Optional

from .. import db

log = logging.getLogger("bridge.remote_control.outbox")


def enqueue(
    payload: dict[str, Any],
    *,
    command_id: Optional[str] = None,
    publication_id: Optional[int] = None,
    event_id: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    """Идемпотентно кладёт событие в outbox; возвращает event_id или None."""
    event_id = event_id or str(uuid.uuid4())
    timestamp = int(now if now is not None else time.time())
    try:
        with db.conn() as connection:
            connection.execute(
                """INSERT INTO rc_event_outbox
                   (event_id, command_id, publication_id, payload, attempts,
                    next_attempt_at, created_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)
                   ON CONFLICT(event_id) DO NOTHING""",
                (
                    event_id,
                    command_id,
                    publication_id,
                    json.dumps(payload, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return event_id
    except (sqlite3.Error, TypeError, ValueError, OSError) as error:
        log.warning("RC outbox enqueue failed (%s)", type(error).__name__)
        return None


def next_due(*, now: Optional[int] = None) -> Optional[dict[str, Any]]:
    timestamp = int(now if now is not None else time.time())
    with db.conn() as connection:
        row = connection.execute(
            """SELECT event_id, command_id, payload, attempts
               FROM rc_event_outbox
               WHERE next_attempt_at <= ?
               ORDER BY created_at, event_id
               LIMIT 1""",
            (timestamp,),
        ).fetchone()
    return dict(row) if row else None


def mark_delivered(event_id: str) -> None:
    with db.conn() as connection:
        connection.execute("DELETE FROM rc_event_outbox WHERE event_id=?", (event_id,))


def mark_retry(event_id: str, attempts: int, reason: str, *, now: Optional[int] = None) -> None:
    timestamp = int(now if now is not None else time.time())
    next_attempts = attempts + 1
    delay = min(3600, 5 * (2 ** min(next_attempts, 9)))
    with db.conn() as connection:
        connection.execute(
            """UPDATE rc_event_outbox
               SET attempts=?, next_attempt_at=?, last_error=?
               WHERE event_id=?""",
            (next_attempts, timestamp + delay, reason[:120], event_id),
        )


def pending_count() -> int:
    with db.conn() as connection:
        row = connection.execute("SELECT COUNT(*) AS n FROM rc_event_outbox").fetchone()
    return int(row["n"]) if row else 0
