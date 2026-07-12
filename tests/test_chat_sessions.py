import json
import os
from pathlib import Path
from typing import cast

import pytest

from chat_sessions import AccountRecord, Session, claude_sessions_dir, list_resumable
from core import config


def account(home: Path, *, provider: str = "claude_code") -> AccountRecord:
    return cast(
        AccountRecord,
        {
            "label": "main",
            "provider": provider,
            "default_model": "model-x",
            "cli_home_path": str(home),
        },
    )


def session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str = "claude_code",
) -> Session:
    monkeypatch.setattr(config, "user_default_cwd", lambda _user_id: "/work/project")
    return Session(account(tmp_path, provider=provider), 42, "@user")


def test_session_initializes_identity_model_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = session(tmp_path, monkeypatch)

    assert current.label == "main"
    assert current.provider == "claude_code"
    assert current.model == "model-x"
    assert current.cwd == "/work/project"
    assert current.session_id is None
    assert current.last_meta == {}


def test_non_claude_provider_has_no_native_resume_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = session(tmp_path, monkeypatch, provider="gemini")

    assert claude_sessions_dir(current) is None
    assert list_resumable(current) == []


def test_resume_store_reads_first_user_text_and_sorts_by_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = session(tmp_path, monkeypatch)
    directory = tmp_path / "projects" / "-work-project"
    directory.mkdir(parents=True)
    older = directory / "older.jsonl"
    newer = directory / "newer.jsonl"
    older.write_text(
        "not-json\n"
        + json.dumps({"type": "user", "isMeta": True, "message": {"content": "hidden"}})
        + "\n"
        + json.dumps({"type": "user", "message": {"content": " Older prompt "}})
        + "\n",
        encoding="utf-8",
    )
    newer.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "image", "data": "ignored"},
                        {"type": "text", "text": "Newest prompt"},
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    items = list_resumable(current)

    assert [item.session_id for item in items] == ["newer", "older"]
    assert [item.title for item in items] == ["Newest prompt", "Older prompt"]
    assert items[0].mtime == 200


def test_resume_store_bounds_title_and_result_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = session(tmp_path, monkeypatch)
    directory = tmp_path / "projects" / "-work-project"
    directory.mkdir(parents=True)
    for index in range(3):
        (directory / f"{index}.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "x" * 100}}) + "\n",
            encoding="utf-8",
        )

    items = list_resumable(current, limit=2)

    assert len(items) == 2
    assert all(len(item.title) == 70 for item in items)


def test_resume_store_uses_placeholder_for_unreadable_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = session(tmp_path, monkeypatch)
    directory = tmp_path / "projects" / "-work-project"
    directory.mkdir(parents=True)
    (directory / "empty.jsonl").write_text(
        json.dumps({"type": "assistant", "message": {"content": "answer"}}) + "\n",
        encoding="utf-8",
    )

    assert list_resumable(current)[0].title == "(без текста)"
