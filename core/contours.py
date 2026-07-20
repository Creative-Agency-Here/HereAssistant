"""User-scoped liveness heartbeats for local/server VS Code installations."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from . import db

ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
KINDS = frozenset({"local", "server", "remote"})
STATES = frozenset({"open", "working", "closed"})


class ContourError(ValueError):
    pass


def _one_line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def heartbeat(user_id: int, payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContourError("invalid_payload")
    contour_id = _one_line(payload.get("id"), 120)
    label = _one_line(payload.get("label"), 80)
    kind = _one_line(payload.get("kind"), 20).lower()
    state = _one_line(payload.get("state"), 20).lower()
    try:
        task_count = max(0, min(999, int(payload.get("taskCount") or 0)))
    except (TypeError, ValueError) as error:
        raise ContourError("invalid_task_count") from error
    if not ID_PATTERN.fullmatch(contour_id) or not label:
        raise ContourError("invalid_identity")
    if kind not in KINDS or state not in STATES:
        raise ContourError("invalid_state")
    now = int(time.time())
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO contour_heartbeats
               (user_id,contour_id,label,kind,state,task_count,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id,contour_id) DO UPDATE SET
                 label=excluded.label,kind=excluded.kind,state=excluded.state,
                 task_count=excluded.task_count,updated_at=excluded.updated_at""",
            (int(user_id), contour_id, label, kind, state, task_count, now),
        )
    return {
        "id": contour_id,
        "label": label,
        "kind": kind,
        "state": state,
        "taskCount": task_count,
        "updatedAt": now,
    }


def close(user_id: int, contour_id: str) -> bool:
    normalized = _one_line(contour_id, 120)
    if not ID_PATTERN.fullmatch(normalized):
        raise ContourError("invalid_identity")
    with db.conn() as connection:
        cursor = connection.execute(
            """UPDATE contour_heartbeats SET state='closed',task_count=0,updated_at=?
               WHERE user_id=? AND contour_id=?""",
            (int(time.time()), int(user_id), normalized),
        )
    return bool(cursor.rowcount)


def list_for_user(user_id: int, *, live_after_sec: int = 45) -> list[dict[str, Any]]:
    now = int(time.time())
    with db.conn() as connection:
        rows = connection.execute(
            """SELECT contour_id,label,kind,state,task_count,updated_at
               FROM contour_heartbeats WHERE user_id=? ORDER BY updated_at DESC""",
            (int(user_id),),
        ).fetchall()
    result = []
    for row in rows:
        fresh = now - int(row["updated_at"]) <= live_after_sec
        state = str(row["state"]) if fresh else "closed"
        result.append(
            {
                "id": str(row["contour_id"]),
                "label": str(row["label"]),
                "kind": str(row["kind"]),
                "originHost": str(row["contour_id"]),
                "local": False,
                "state": state,
                "estimated": False,
                "sessions": 0,
                "taskCount": int(row["task_count"]) if state != "closed" else 0,
                "lastActivityAt": datetime.fromtimestamp(
                    int(row["updated_at"]), tz=UTC
                ).isoformat(),
            }
        )
    return result
