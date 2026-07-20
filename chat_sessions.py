"""Typed session state и native Claude resume store для terminal chat."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from core import config
from core.models import AccountLike

AccountRecord = AccountLike


class ResumableSession(NamedTuple):
    session_id: str
    title: str
    mtime: float


class Session:
    def __init__(self, account: AccountRecord, user_id: int, user_name: str = "") -> None:
        self.account = account
        self.user_id = user_id
        self.user_name = user_name
        self.model: str | None = account["default_model"]
        self.cwd = config.user_default_cwd(user_id)
        self.session_id: str | None = None
        self.last_meta: Mapping[str, Any] = {}
        # Stable only for this terminal chat. CRM turns it into a deterministic
        # UUID and keeps subsequent prompts in one conversation.
        self.crm_conversation_id = uuid.uuid4().int % (2**63 - 1)

    @property
    def label(self) -> str:
        return str(self.account["label"])

    @property
    def provider(self) -> str:
        return str(self.account["provider"])


def claude_sessions_dir(session: Session) -> Path | None:
    if session.provider != "claude_code":
        return None
    slug = str(session.cwd).replace("/", "-").replace("\\", "-")
    directory = Path(session.account["cli_home_path"]) / "projects" / slug
    return directory if directory.exists() else None


def list_resumable(session: Session, *, limit: int = 20) -> list[ResumableSession]:
    directory = claude_sessions_dir(session)
    if directory is None:
        return []
    candidates: list[tuple[Path, float]] = []
    for path in directory.glob("*.jsonl"):
        try:
            candidates.append((path, path.stat().st_mtime))
        except OSError:
            continue
    candidates.sort(key=lambda item: item[1], reverse=True)
    return [
        ResumableSession(path.stem, _session_title(path), mtime)
        for path, mtime in candidates[:limit]
    ]


def _session_title(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(event, Mapping):
                    continue
                if event.get("type") != "user" or event.get("isMeta"):
                    continue
                message = event.get("message")
                if not isinstance(message, Mapping):
                    continue
                title = _content_text(message.get("content"))
                if title.strip():
                    return title.strip()[:70]
    except OSError:
        return "(без текста)"
    return "(без текста)"


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                return str(block.get("text") or "")
    return ""
