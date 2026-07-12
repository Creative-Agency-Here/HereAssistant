import json
import os
import sqlite3
from pathlib import Path

import pytest

from core import config, db, rtk


def create_history(cli_home: Path) -> Path:
    environment = rtk.runtime_env(cli_home)
    database = Path(environment["RTK_DB_PATH"])
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE commands (
               id INTEGER PRIMARY KEY, timestamp TEXT, original_cmd TEXT, rtk_cmd TEXT,
               input_tokens INTEGER, output_tokens INTEGER, saved_tokens INTEGER,
               savings_pct REAL, exec_time_ms INTEGER, project_path TEXT)"""
        )
        connection.execute(
            """INSERT INTO commands
               (timestamp,original_cmd,rtk_cmd,input_tokens,output_tokens,saved_tokens,
                savings_pct,exec_time_ms,project_path)
               VALUES (datetime('now'),'git status --secret token','rtk git status --secret token',
                       100,20,80,80,4,'/private/project')"""
        )
    return database


def test_runtime_env_is_per_account_and_cleanup_redacts_history(tmp_path: Path) -> None:
    cli_home = tmp_path / "account"
    database = create_history(cli_home)
    tee_secret = cli_home / ".rtk" / "tee" / "raw.log"
    tee_secret.write_text("private output", encoding="utf-8")

    rtk.sanitize_runtime(cli_home)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT original_cmd,rtk_cmd,project_path,input_tokens,saved_tokens FROM commands"
        ).fetchone()
    assert row == ("git", "rtk git", "", 100, 80)
    assert not tee_secret.exists()
    if os.name != "nt":
        assert database.stat().st_mode & 0o777 == 0o600


def test_configure_claude_profile_is_idempotent_and_preserves_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "claude"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    monkeypatch.setattr(rtk.shutil, "which", lambda _name: "/usr/local/bin/rtk")

    assert rtk.configure_claude_profile(home)
    assert rtk.configure_claude_profile(home)

    payload = json.loads(settings.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    commands = [
        hook["command"] for entry in payload["hooks"]["PreToolUse"] for hook in entry["hooks"]
    ]
    assert commands == ["rtk hook claude"]
    assert "Bash(rtk git status:*)" in payload["permissions"]["allow"]
    if os.name != "nt":
        assert settings.stat().st_mode & 0o777 == 0o600


def test_user_savings_uses_only_enabled_owned_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(rtk.shutil, "which", lambda _name: "/usr/local/bin/rtk")
    db.init()
    owned_home = tmp_path / "owned"
    foreign_home = tmp_path / "foreign"
    create_history(owned_home)
    create_history(foreign_home)
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO accounts
               (provider,label,cli_home_path,enabled,owner_user_id,shared)
               VALUES ('claude_code','owned',?,1,100,0),
                      ('claude_code','foreign',?,1,200,0),
                      ('claude_code','shared',?,1,NULL,1)""",
            (str(owned_home), str(foreign_home), str(foreign_home)),
        )

    result = rtk.user_savings(100)

    assert result["available"]
    assert result["accounts"] == 1
    assert result["commands"] == 1
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 20
    assert result["saved_tokens"] == 80
    assert result["savings_pct"] == 80.0


def test_user_savings_reads_sanitized_runner_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        "OS_RUNNER_METRICS_DIR": tmp_path / "metrics",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(config, "OS_RUNNERS_ENABLED", True)
    monkeypatch.setattr(rtk.shutil, "which", lambda _name: "/usr/local/bin/rtk")
    db.init()
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO accounts
               (provider,label,cli_home_path,enabled,owner_user_id,shared)
               VALUES ('claude_code','owned','/private/unreadable',1,100,0)"""
        )
    snapshot = config.OS_RUNNER_METRICS_DIR / "100" / "claude_code.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        json.dumps(
            {
                "commands": 3,
                "input_tokens": 100,
                "output_tokens": 40,
                "saved_tokens": 60,
                "today_commands": 2,
                "today_saved_tokens": 30,
            }
        ),
        encoding="utf-8",
    )

    result = rtk.user_savings(100)

    assert result["commands"] == 3
    assert result["saved_tokens"] == 60
    assert result["savings_pct"] == 60.0
    assert result["today_commands"] == 2
