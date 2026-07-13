from io import StringIO
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from chat_commands import CommandRouter
from chat_identity import UserRecord
from chat_sessions import AccountRecord, ResumableSession, Session
from core import config


def account(
    *,
    label: str = "main",
    provider: str = "claude_code",
    model: str = "model-a",
    enabled: bool = True,
) -> AccountRecord:
    return cast(
        AccountRecord,
        {
            "label": label,
            "provider": provider,
            "default_model": model,
            "enabled": enabled,
            "cli_home_path": "/tmp/home",
        },
    )


def setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    read: str = "",
    resumable: list[ResumableSession] | None = None,
) -> tuple[Session, CommandRouter, StringIO, MagicMock]:
    monkeypatch.setattr(config, "user_default_cwd", lambda _user_id: "/workspace/1")
    session = Session(account(), 1, "@alice")
    session.session_id = "current-session"
    output = StringIO()
    clear = MagicMock(return_value=0)
    users = cast(
        list[UserRecord],
        [
            {"telegram_id": 1, "username": "alice"},
            {"telegram_id": 2, "username": "bob"},
        ],
    )
    secondary = account(label="secondary", provider="gemini", model="model-b")
    bob = account(label="bob-main", model="model-b")
    router = CommandRouter(
        accounts=lambda user_id: [account(), secondary] if user_id == 1 else [bob],
        users=lambda: users,
        default_cwd=lambda user_id: f"/workspace/{user_id}",
        resumable=lambda _session: resumable or [],
        output=output,
        read=lambda _prompt: read,
        system=clear,
    )
    return session, router, output, clear


@pytest.mark.parametrize("command", ["/exit", "/QUIT", "/q"])
def test_exit_aliases_stop_repl(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    session, router, _, _ = setup(monkeypatch)

    assert not router.handle(session, command)


def test_model_and_account_changes_reset_provider_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session, router, output, _ = setup(monkeypatch)

    assert router.handle(session, "/model custom")
    assert session.model == "custom"
    assert session.session_id == "current-session"

    assert router.handle(session, "/account secondary")
    assert session.label == "secondary"
    assert session.provider == "gemini"
    assert session.model == "model-b"
    assert session.session_id is None
    assert "сессия сброшена" in output.getvalue()


def test_user_change_resets_identity_workspace_and_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session, router, _, _ = setup(monkeypatch)

    router.handle(session, "/user @BOB")

    assert session.user_id == 2
    assert session.user_name == "@bob"
    assert session.label == "bob-main"
    assert session.model == "model-b"
    assert session.cwd == "/workspace/2"
    assert session.session_id is None


def test_user_change_is_rejected_without_accessible_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, router, output, _ = setup(monkeypatch)
    router.accounts = lambda _user_id: []

    router.handle(session, "/user @bob")

    assert session.user_id == 1
    assert "нет доступных аккаунтов" in output.getvalue()


def test_cwd_accepts_directory_and_rejects_missing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, router, output, _ = setup(monkeypatch)

    router.handle(session, f"/cwd {tmp_path}")
    assert session.cwd == str(tmp_path.resolve())
    assert session.session_id is None

    router.handle(session, "/cwd /definitely/missing/path")
    assert "нет такой папки" in output.getvalue()


def test_resume_selects_typed_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        ResumableSession("first-session", "First", 100),
        ResumableSession("second-session", "Second", 200),
    ]
    session, router, output, _ = setup(monkeypatch, read="2", resumable=items)

    router.handle(session, "/resume")

    assert session.session_id == "second-session"
    assert "продолжаю сессию second-s" in output.getvalue()


def test_diff_is_bounded_to_forty_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    session, router, output, _ = setup(monkeypatch)
    session.last_meta = {
        "edits": [
            {
                "file": "app.py",
                "added": 50,
                "removed": 50,
                "old": "\n".join(f"old-{index}" for index in range(50)),
                "new": "\n".join(f"new-{index}" for index in range(50)),
            }
        ]
    }

    router.handle(session, "/diff")

    rendered = output.getvalue()
    assert "old-39" in rendered and "old-40" not in rendered
    assert "new-39" in rendered and "new-40" not in rendered
    assert "обрезано" in rendered


def test_clear_and_unknown_command_are_boundary_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    session, router, output, clear = setup(monkeypatch)

    router.handle(session, "/clear")
    router.handle(session, "/unknown")

    clear.assert_called_once()
    assert "неизвестная команда /unknown" in output.getvalue()
