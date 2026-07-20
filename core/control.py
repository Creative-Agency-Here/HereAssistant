"""Small SQLite control plane shared by the Web API and Telegram bot process."""

from __future__ import annotations

import json
import time
from typing import Any

from . import db


def request_stop(user_id: int) -> int:
    now = int(time.time())
    with db.conn() as connection:
        existing = connection.execute(
            """SELECT id FROM control_requests
               WHERE user_id=? AND action='stop' AND status='pending'
               ORDER BY id DESC LIMIT 1""",
            (int(user_id),),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        cursor = connection.execute(
            "INSERT INTO control_requests(user_id,action,status,created_at) VALUES (?,'stop','pending',?)",
            (int(user_id), now),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("control_request_not_created")
        return int(cursor.lastrowid)


def pending(limit: int = 20) -> list[dict[str, Any]]:
    with db.conn() as connection:
        rows = connection.execute(
            """SELECT id,user_id,action,created_at FROM control_requests
               WHERE status='pending' ORDER BY created_at,id LIMIT ?""",
            (max(1, min(100, limit)),),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_handled(request_id: int, *, cancelled: int = 0, failed: bool = False) -> None:
    with db.conn() as connection:
        connection.execute(
            """UPDATE control_requests SET status=?,result=?,handled_at=?
               WHERE id=? AND status='pending'""",
            (
                "failed" if failed else "handled",
                json.dumps({"cancelled": max(0, int(cancelled))}, separators=(",", ":")),
                int(time.time()),
                int(request_id),
            ),
        )
