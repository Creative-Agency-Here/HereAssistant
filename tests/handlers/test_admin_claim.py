import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, Message

from core import access, config, db
from handlers import admin_claim, team


@pytest.fixture
def isolated_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / ".runtime"
    for name, value in {
        "RUNTIME_DIR": runtime,
        "DOWNLOADS_DIR": runtime / "downloads",
        "LOGS_DIR": runtime / "logs",
        "BACKUPS_DIR": runtime / "backups",
        "STATE_DIR": runtime / "state",
        "CLI_HOMES_DIR": runtime / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
        "RESTART_REQUEST_FILE": runtime / "state" / "restart_request.json",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(config, "CLAIM_CODE", "claim-code")
    monkeypatch.setattr(admin_claim.events, "log", MagicMock())
    db.init()
    return config.DB_PATH


def message(uid: int = 100) -> MagicMock:
    value = MagicMock(spec=Message)
    value.from_user = SimpleNamespace(id=uid, username=f"user{uid}")
    value.chat = SimpleNamespace(id=500)
    value.message_thread_id = None
    value.answer = AsyncMock()
    return value


def command(args: str | None) -> CommandObject:
    return cast(CommandObject, SimpleNamespace(args=args))


@pytest.mark.asyncio
async def test_first_owner_claim_promotes_pending_row_and_updates_runtime(
    isolated_claim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    msg = message()
    access.upsert_seen(100, "pending", "Pending")
    persist = MagicMock()
    monkeypatch.setattr(admin_claim, "_persist_admin_id", persist)

    await admin_claim.cmd_start(cast(Message, msg), command("claim-code"))

    row = access.get_user(100)
    assert config.ADMIN_ID == 100
    assert config.ADMIN_IDS == [100]
    assert row["role"] == "admin" and row["status"] == "approved"
    persist.assert_called_once_with(100)
    assert "владелец" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_invalid_claim_does_not_mutate_owner(
    isolated_claim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    msg = message()
    persist = MagicMock()
    monkeypatch.setattr(admin_claim, "_persist_admin_id", persist)

    await admin_claim.cmd_start(cast(Message, msg), command("wrong"))

    assert config.ADMIN_ID is None and config.ADMIN_IDS == []
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_approved_user_logout_revokes_database_access(isolated_claim: Path) -> None:
    access.upsert_seen(200, "user", "User")
    access.approve(200)
    msg = message(200)

    await admin_claim.cmd_logout(cast(Message, msg), command("confirm"))

    assert access.get_user(200)["status"] == "denied"
    assert "доступ снят" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_last_owner_logout_rotates_claim_and_requests_restart(
    isolated_claim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "ADMIN_IDS", [100])
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    access.upsert_seen(100, "owner", "Owner")
    access.promote(100)
    remove = MagicMock()
    append = MagicMock()
    monkeypatch.setattr(config, "remove_env_admin", remove)
    monkeypatch.setattr(config, "append_env", append)
    monkeypatch.setattr(admin_claim.secrets, "token_urlsafe", lambda _size: "new-code")
    msg = message(100)

    await admin_claim.cmd_logout(cast(Message, msg), command("confirm"))

    assert config.ADMIN_ID is None and config.ADMIN_IDS == []
    assert config.CLAIM_CODE == "new-code"
    assert access.get_user(100)["status"] == "denied"
    remove.assert_called_once_with(100)
    append.assert_called_once_with("CLAIM_CODE", "new-code")
    payload = json.loads(config.RESTART_REQUEST_FILE.read_text(encoding="utf-8"))
    assert payload["reason"].startswith("/logout")


@pytest.mark.asyncio
async def test_admin_cannot_apply_role_action_to_self(
    isolated_claim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    access.upsert_seen(300, "admin", "Admin")
    access.promote(300)
    query = MagicMock(spec=CallbackQuery)
    query.from_user = SimpleNamespace(id=300)
    query.data = "usr:promote:300"
    query.answer = AsyncMock()
    event_log = MagicMock()
    monkeypatch.setattr(team.events, "log", event_log)

    await team._apply_role_action(cast(CallbackQuery, query), "promote")

    query.answer.assert_awaited_once_with(
        "Свою роль менять нельзя — попроси другого админа", show_alert=True
    )
    event_log.assert_not_called()
