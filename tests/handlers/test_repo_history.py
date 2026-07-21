from pathlib import Path

import pytest

from core import config, db
from handlers import repo


def test_cross_provider_history_keeps_provider_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / ".runtime")
    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / ".runtime" / "downloads")
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / ".runtime" / "logs")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / ".runtime" / "backups")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / ".runtime" / "state")
    monkeypatch.setattr(config, "CLI_HOMES_DIR", tmp_path / ".runtime" / "cli_homes")
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(config, "DEFAULT_PROJECT_DIR", tmp_path / "workspace" / "default")
    monkeypatch.setattr(config, "ADMIN_IDS", [100])
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    db.init()
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO conversations
               (id, user_id, chat_id, thread_id, created_at, updated_at)
               VALUES (1, 100, 10, 0, 1, 1)"""
        )
    repo.save_message(1, "user", "Начали задачу")
    repo.save_message(1, "assistant", "Сделал анализ", provider="claude_code", model="opus")
    repo.save_message(1, "assistant", "Добавил тест", provider="codex", model="gpt-5")
    with db.conn() as connection:
        conversation = connection.execute("SELECT * FROM conversations WHERE id = 1").fetchone()
    assert conversation is not None

    prompt = repo.build_prompt_with_history(conversation, "Продолжай")

    assert "Ассистент (claude_code/opus): Сделал анализ" in prompt
    assert "Ассистент (codex/gpt-5): Добавил тест" in prompt
    assert prompt.endswith("Текущий запрос пользователя: Продолжай")
