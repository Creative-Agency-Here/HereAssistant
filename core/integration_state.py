"""Atomic local state exchange between terminal chat and IDE integrations."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import config

ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
STATES = frozenset({"open", "working", "error", "closed"})


def state_path(integration_id: str) -> Path:
    if not ID_PATTERN.fullmatch(integration_id):
        raise ValueError("Некорректный integration id")
    return config.STATE_DIR / "integrations" / f"{integration_id}.json"


def write(
    integration_id: str,
    *,
    state: str,
    cwd: str,
    task_count: int = 0,
    title: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if state not in STATES:
        raise ValueError("Некорректное состояние интеграции")
    path = state_path(integration_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "state": state,
        "cwd": str(Path(cwd).resolve()),
        "taskCount": max(0, min(999, int(task_count))),
        "title": " ".join(str(title or "").split())[:120] or None,
        "sessionId": str(session_id or "")[:160] or None,
        "updatedAt": int(time.time()),
    }
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    return payload
