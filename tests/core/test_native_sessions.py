from __future__ import annotations

import json
from pathlib import Path

from core import config, db, native_sessions


def write_policy(root: Path, *, content: bool = False) -> None:
    path = root / ".hereassistant" / "project.yml"
    path.parent.mkdir()
    path.write_text(
        "\n".join(
            [
                "name: Test project",
                "mode: crm",
                "crm_project_id: project-1",
                "sync:",
                "  enabled: true",
                f"  send_prompts: {str(content).lower()}",
                f"  send_messages: {str(content).lower()}",
            ]
        ),
        encoding="utf-8",
    )


def prepare_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(config, "ADMIN_ID", 123456789)
    monkeypatch.setattr(config, "ADMIN_IDS", [123456789])
    db.init()


def outbox_payloads() -> list[dict]:
    with db.conn() as connection:
        rows = connection.execute("SELECT payload FROM crm_sync_outbox").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def test_unconfigured_folder_is_private(tmp_path: Path) -> None:
    result = native_sessions.ingest_hook(
        "qwen_code",
        {"cwd": str(tmp_path), "session_id": "session-1"},
        env={"HEREASSISTANT_NATIVE_USER_ID": "123"},
        now=100,
    )

    assert result.state == "private"
    assert result.project_root is None


def test_metadata_only_policy_does_not_read_transcript(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_policy(project, content=False)
    prepare_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        native_sessions,
        "_transcript_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("transcript read")),
    )

    result = native_sessions.ingest_hook(
        "qwen_code",
        {
            "cwd": str(project),
            "session_id": "session-2",
            "transcript_path": str(tmp_path / "must-not-be-read.jsonl"),
            "model": "qwen-test",
        },
        now=200,
    )

    assert result.state == "queued"
    payload = outbox_payloads()[0]
    assert payload["messages"] == []
    assert payload["model"] == "qwen-test"


def test_opted_in_transcript_is_parsed_only_inside_provider_home(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_policy(project, content=True)
    prepare_db(tmp_path, monkeypatch)
    qwen_home = tmp_path / ".qwen"
    qwen_home.mkdir()
    transcript = qwen_home / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "question"}}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": "answer", "model": "qwen-test"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = native_sessions.ingest_hook(
        "qwen_code",
        {
            "cwd": str(project),
            "session_id": "session-3",
            "transcript_path": str(transcript),
        },
        env={"HOME": str(tmp_path), "HEREASSISTANT_NATIVE_USER_ID": "123456789"},
        now=300,
    )

    assert result.state == "queued"
    payload = outbox_payloads()[0]
    assert [item["content"] for item in payload["messages"]] == ["question", "answer"]
    assert payload["model"] == "qwen-test"


def test_transcript_outside_provider_home_is_never_read(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_policy(project, content=True)
    prepare_db(tmp_path, monkeypatch)
    qwen_home = tmp_path / "home" / ".qwen"
    qwen_home.mkdir(parents=True)
    outside = tmp_path / "secret.jsonl"
    outside.write_text('{"type":"user","content":"must-not-leak"}\n', encoding="utf-8")

    result = native_sessions.ingest_hook(
        "qwen_code",
        {
            "cwd": str(project),
            "session_id": "session-4",
            "transcript_path": str(outside),
        },
        env={"HOME": str(tmp_path / "home"), "HEREASSISTANT_NATIVE_USER_ID": "123456789"},
        now=400,
    )

    assert result.state == "queued"
    assert outbox_payloads()[0]["messages"] == []


def test_gemini_direct_prompt_and_response_are_supported(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_policy(project, content=True)
    prepare_db(tmp_path, monkeypatch)

    result = native_sessions.ingest_hook(
        "gemini",
        {
            "cwd": str(project),
            "session_id": "gemini-session",
            "prompt": "gemini question",
            "prompt_response": "gemini answer",
            "model": "gemini-test",
        },
        env={
            "HOME": str(tmp_path),
            "HEREASSISTANT_NATIVE_USER_ID": "123456789",
            "TERM_PROGRAM": "vscode",
        },
        now=500,
    )

    assert result.state == "queued"
    payload = outbox_payloads()[0]
    assert payload["clientSurface"] == "native_cli"
    assert payload["terminalApp"] == "vscode"
    assert [item["content"] for item in payload["messages"]] == [
        "gemini question",
        "gemini answer",
    ]


def test_repeated_native_hook_creates_one_outbox_event(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_policy(project, content=False)
    prepare_db(tmp_path, monkeypatch)
    payload = {"cwd": str(project), "session_id": "same-session", "model": "test"}

    first = native_sessions.ingest_hook("codex", payload, now=600)
    second = native_sessions.ingest_hook("codex", payload, now=600)

    assert first.event_id == second.event_id
    assert len(outbox_payloads()) == 1


def test_terminal_detection_covers_common_apps() -> None:
    assert native_sessions.terminal_app({"TERM_PROGRAM": "vscode"}) == "vscode"
    assert native_sessions.terminal_app({"GHOSTTY_RESOURCES_DIR": "/tmp"}) == "ghostty"
    assert native_sessions.terminal_app({"WT_SESSION": "id"}) == "windows_terminal"
