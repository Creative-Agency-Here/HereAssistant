"""Typed session state и native Claude resume store для terminal chat."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from core import config, session_import
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
        # Для Codex: наследовать профиль аккаунта либо явно ограничить sandbox.
        # HereAssistant пока запускает codex exec неинтерактивно, поэтому режимы
        # не имитируют диалог одобрения отдельной команды.
        self.permission_mode = "account"
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
    directory = session_import.claude_project_dir(
        Path(session.account["cli_home_path"]), session.cwd
    )
    return directory if directory.exists() else None


def list_resumable(session: Session, *, limit: int = 20) -> list[ResumableSession]:
    """Свежие сессии текущего проекта у выбранного аккаунта.

    Claude хранит их в каталоге, имя которого закодировано из cwd; Codex — в
    общем дереве по датам, поэтому проект берётся из `session_meta`. Провайдеры
    без нативного resume отдают пустой список.
    """
    cli_home = Path(session.account["cli_home_path"])
    if session.provider == "claude_code":
        found = session_import.list_claude_sessions(cli_home, session.cwd, limit=limit)
    elif session.provider == "codex":
        found = session_import.list_codex_sessions(cli_home, session.cwd, limit=limit)
    else:
        return []
    return [ResumableSession(item.session_id, item.title, item.mtime) for item in found]
