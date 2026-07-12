import sqlite3
import time
from pathlib import Path

import pytest

from core import config, db, projects
from handlers import repo
from providers.gemini import _owned_claude_home


@pytest.fixture
def isolation_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    db.init()
    return config.DB_PATH


def add_account(
    path: Path,
    *,
    label: str,
    owner_user_id: int | None,
    shared: bool = False,
    enabled: bool = True,
) -> int:
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """INSERT INTO accounts
               (provider,label,cli_home_path,default_model,enabled,owner_user_id,shared)
               VALUES ('claude_code', ?, ?, 'model', ?, ?, ?)""",
            (label, f"/tmp/{label}", int(enabled), owner_user_id, int(shared)),
        )
        account_id = cursor.lastrowid
        assert account_id is not None
        return account_id


def test_account_selection_never_falls_back_to_foreign_or_unassigned(
    isolation_db: Path,
) -> None:
    own = add_account(isolation_db, label="own", owner_user_id=100)
    foreign = add_account(isolation_db, label="foreign", owner_user_id=200)
    shared = add_account(isolation_db, label="shared", owner_user_id=None, shared=True)
    unassigned = add_account(isolation_db, label="unassigned", owner_user_id=None)

    first = repo.get_or_create_conv(10, 0, 100)
    shared_conv = repo.get_or_create_conv(20, 0, 300)

    assert first["account_id"] == own
    assert shared_conv["account_id"] == shared
    assert {row["id"] for row in repo.list_accounts(100)} == {own, shared}
    assert repo.get_account(foreign, 100) is None
    assert repo.get_account(unassigned, 100) is None
    assert repo.get_account_by_label("foreign", 100) is None
    assert _owned_claude_home(100) == Path("/tmp/own")
    assert _owned_claude_home(300) is None

    with sqlite3.connect(isolation_db) as connection:
        connection.execute(
            "UPDATE conversations SET account_id=? WHERE id=?", (foreign, first["id"])
        )
        connection.execute("UPDATE accounts SET enabled=0 WHERE id=?", (own,))
    normalized = repo.get_or_create_conv(10, 0, 100)
    assert normalized["account_id"] == shared

    with sqlite3.connect(isolation_db) as connection:
        connection.execute("UPDATE accounts SET enabled=0 WHERE id=?", (shared,))
    unavailable = repo.get_or_create_conv(30, 0, 400)
    assert unavailable["account_id"] is None


def test_conversations_with_same_chat_and_thread_are_separate_by_user(
    isolation_db: Path,
) -> None:
    add_account(isolation_db, label="one", owner_user_id=100)
    add_account(isolation_db, label="two", owner_user_id=200)

    first = repo.get_or_create_conv(777, 42, 100)
    second = repo.get_or_create_conv(777, 42, 200)

    assert first["id"] != second["id"]
    assert first["user_id"] == 100
    assert second["user_id"] == 200


def test_authorized_project_path_blocks_other_user_parent_and_symlink(
    isolation_db: Path,
) -> None:
    root = config.user_workspace(100) / "safe"
    nested = root / "nested"
    nested.mkdir(parents=True)
    outside = root.parent / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    project = projects.register_owned_project(100, "safe", root)

    assert projects.resolve_authorized_project_path(100, project["id"], "nested") == nested
    with pytest.raises(projects.ProjectAccessError, match="выходит за пределы"):
        projects.resolve_authorized_project_path(100, project["id"], "../outside")
    with pytest.raises(projects.ProjectAccessError, match="выходит за пределы"):
        projects.resolve_authorized_project_path(100, project["id"], "escape")
    with pytest.raises(projects.ProjectNotFoundError):
        projects.resolve_authorized_project_path(200, project["id"], ".")


def test_shared_project_requires_explicit_membership(isolation_db: Path, tmp_path: Path) -> None:
    root = tmp_path / "shared-project"
    root.mkdir()
    project = projects.register_owned_project(200, "shared", root)
    now = int(time.time())
    with sqlite3.connect(isolation_db) as connection:
        connection.execute("UPDATE projects SET visibility='shared' WHERE id=?", (project["id"],))

    assert projects.get_accessible_project(100, project["id"]) is None

    with sqlite3.connect(isolation_db) as connection:
        connection.execute(
            "INSERT INTO project_members(project_id,user_id,role,created_at) VALUES (?,?,?,?)",
            (project["id"], 100, "developer", now),
        )

    assert projects.resolve_authorized_project_path(100, project["id"], ".") == root
