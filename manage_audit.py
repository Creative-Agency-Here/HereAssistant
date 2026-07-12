"""Read-only audit queries и форматирование для manager UI."""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import TypedDict

log = logging.getLogger("hereassistant.manage.audit")


class UsageState(TypedDict):
    msgs: int
    tokens: int
    limited: bool
    reset: str | int | None


class AuditEntry(TypedDict):
    timestamp: int
    event_type: str
    user_id: int | None
    account_label: str | None
    tokens: int


def account_usage(db_path: Path, label: str, *, now: int | None = None) -> UsageState:
    current = now if now is not None else int(time.time())
    result: UsageState = {"msgs": 0, "tokens": 0, "limited": False, "reset": None}
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            usage = connection.execute(
                "SELECT COUNT(*) n, "
                "COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0) tok "
                "FROM events WHERE account_label=? AND event_type='message_out' "
                "AND timestamp>=?",
                (label, current - 5 * 3600),
            ).fetchone()
            if usage is not None:
                result["msgs"] = int(usage["n"] or 0)
                result["tokens"] = int(usage["tok"] or 0)
            rate_limit = connection.execute(
                "SELECT payload,timestamp FROM events WHERE account_label=? "
                "AND (event_type='rate_limit' OR payload LIKE '%rate_limit%') "
                "ORDER BY id DESC LIMIT 1",
                (label,),
            ).fetchone()
    except sqlite3.Error as error:
        log.debug("account usage unavailable: %s", error)
        return result

    if rate_limit is None or int(rate_limit["timestamp"] or 0) < current - 3600:
        return result
    result["limited"] = True
    try:
        payload = json.loads(rate_limit["payload"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return result
    if isinstance(payload, dict):
        result["reset"] = payload.get("rate_limit_reset") or payload.get("reset")
    return result


def telegram_history(db_path: Path, *, limit: int = 30) -> list[AuditEntry]:
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT timestamp, event_type, user_id, account_label, "
                "COALESCE(tokens_in,0)+COALESCE(tokens_out,0) tok "
                "FROM events WHERE event_type IN ('message_in','message_out','error') "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.Error as error:
        log.debug("telegram audit unavailable: %s", error)
        return []
    return [
        {
            "timestamp": int(row["timestamp"]),
            "event_type": str(row["event_type"]),
            "user_id": row["user_id"],
            "account_label": row["account_label"],
            "tokens": int(row["tok"] or 0),
        }
        for row in rows
    ]


def ssh_history(*, limit: int = 15) -> list[str]:
    try:
        output = subprocess.run(
            ["last", "-a", "-n", str(limit)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        log.debug("ssh audit unavailable: %s", error)
        return []
    return [
        entry
        for entry in output.splitlines()
        if entry.strip() and not entry.lower().startswith("wtmp")
    ][:limit]


def format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1000:
        return f"{value / 1000:.0f}k"
    return str(value)


def format_timestamp(timestamp: int) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(timestamp))
