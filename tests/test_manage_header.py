import io
import sqlite3
from pathlib import Path

import pytest

import manage_header
from manage_header import bot_username, render_header, reset_bot_cache


def create_accounts(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE accounts (id INTEGER PRIMARY KEY, provider TEXT, label TEXT, "
            "cli_home_path TEXT, default_model TEXT, enabled INTEGER, notes TEXT, "
            "owner_user_id INTEGER)"
        )


def test_bot_username_skips_network_without_token(tmp_path: Path, monkeypatch) -> None:
    reset_bot_cache()
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=PASTE_HERE\n", encoding="utf-8")
    urlopen = pytest.fail
    monkeypatch.setattr(manage_header.urllib.request, "urlopen", urlopen)

    assert bot_username(env_path) is None


def test_bot_username_parses_and_caches_get_me(tmp_path: Path, monkeypatch) -> None:
    reset_bot_cache()
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=test-token\n", encoding="utf-8")
    calls = 0

    def urlopen(*_args: object, **_kwargs: object):
        nonlocal calls
        calls += 1
        return io.StringIO('{"ok":true,"result":{"username":"assistant_bot"}}')

    monkeypatch.setattr(manage_header.urllib.request, "urlopen", urlopen)

    assert bot_username(env_path) == "@assistant_bot"
    assert bot_username(env_path) == "@assistant_bot"
    assert calls == 1


def test_render_header_handles_unconfigured_empty_install(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    reset_bot_cache()
    db_path = tmp_path / "db.sqlite3"
    env_path = tmp_path / ".env"
    create_accounts(db_path)
    env_path.write_text("TELEGRAM_BOT_TOKEN=PASTE_HERE\n", encoding="utf-8")
    monkeypatch.setattr(manage_header, "logo", lambda: None)

    render_header(base_dir=tmp_path, env_path=env_path, db_path=db_path)

    rendered = capsys.readouterr().out
    assert "токен не задан" in rendered
    assert "Админ" in rendered and "не задан" in rendered
    assert "Аккаунты" in rendered and "нет" in rendered


def test_render_header_summarizes_accounts_without_exposing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    reset_bot_cache()
    db_path = tmp_path / "db.sqlite3"
    env_path = tmp_path / ".env"
    create_accounts(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO accounts(provider,label,cli_home_path,enabled,owner_user_id) "
            "VALUES (?,?,?,?,?)",
            ("claude_code", "main", "/protected/claude", 1, 123),
        )
    env_path.write_text("TELEGRAM_BOT_TOKEN=PASTE_HERE\n", encoding="utf-8")
    monkeypatch.setattr(manage_header, "logo", lambda: None)

    render_header(base_dir=tmp_path, env_path=env_path, db_path=db_path)

    rendered = capsys.readouterr().out
    assert "активно: 1" in rendered
    assert "подробности [1]" in rendered
    assert "main" not in rendered
