from __future__ import annotations

import json
from pathlib import Path

from core import config, crm_sync, db, project_config


def exchange() -> crm_sync.Exchange:
    return crm_sync.Exchange(
        conversation_id=17,
        telegram_user_id=123456789,
        cwd="/opt/hereassistant/workspace/123456789/project",
        project_name="Project",
        provider="claude_code",
        model="claude-test",
        prompt="Секретный вопрос",
        answer="Полезный ответ",
        started_at=1_700_000_000,
        finished_at=1_700_000_005,
        tokens_in=10,
        tokens_out=20,
        duration_ms=5000,
    )


def policy(**flags: bool) -> project_config.ProjectPolicy:
    return project_config.ProjectPolicy(
        mode="crm",
        name="CRM project",
        crm_project_id="project-1",
        sync_enabled=True,
        sync_flags={f"send_{name}": value for name, value in flags.items()},
    )


def test_private_project_never_builds_outbound_payload() -> None:
    assert crm_sync.build_payload(project_config.PRIVATE, exchange(), event_id="event") is None


def test_prompt_and_answer_have_independent_privacy_gates() -> None:
    prompt_only = crm_sync.build_payload(
        policy(prompts=True, messages=False),
        exchange(),
        event_id="00000000-0000-0000-0000-000000000001",
    )
    assert prompt_only is not None
    assert [message["role"] for message in prompt_only["messages"]] == ["user"]
    assert prompt_only["title"] == "Секретный вопрос"
    assert "Полезный ответ" not in json.dumps(prompt_only, ensure_ascii=False)

    answer_only = crm_sync.build_payload(
        policy(prompts=False, messages=True),
        exchange(),
        event_id="00000000-0000-0000-0000-000000000002",
    )
    assert answer_only is not None
    assert [message["role"] for message in answer_only["messages"]] == ["assistant"]
    assert answer_only["title"] == "CRM project"
    assert "Секретный вопрос" not in json.dumps(answer_only, ensure_ascii=False)


def test_session_identity_is_stable_per_origin_and_conversation(monkeypatch) -> None:
    monkeypatch.setattr(config, "HERECRM_SYNC_ORIGIN", "canary")
    first = crm_sync.build_payload(
        policy(prompts=True),
        exchange(),
        event_id="00000000-0000-0000-0000-000000000001",
    )
    second = crm_sync.build_payload(
        policy(prompts=True),
        exchange(),
        event_id="00000000-0000-0000-0000-000000000002",
    )
    assert first is not None and second is not None
    assert first["sessionId"] == second["sessionId"]
    assert first["eventId"] != second["eventId"]


def test_enqueue_persists_only_opted_in_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", None)
    db.init()

    assert not crm_sync.enqueue(project_config.PRIVATE, exchange())
    assert crm_sync.enqueue(policy(prompts=True, messages=False), exchange())

    with db.conn() as connection:
        rows = connection.execute("SELECT payload FROM crm_sync_outbox").fetchall()
    assert len(rows) == 1
    stored = rows[0]["payload"]
    assert "Секретный вопрос" in stored
    assert "Полезный ответ" not in stored
