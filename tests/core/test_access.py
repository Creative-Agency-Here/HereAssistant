import sqlite3
from pathlib import Path

import pytest

from core import access, config, db


@pytest.fixture
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Полностью изолирует access-тесты от рабочей БД и runtime-каталогов."""
    runtime = tmp_path / ".runtime"
    paths = {
        "RUNTIME_DIR": runtime,
        "DOWNLOADS_DIR": runtime / "downloads",
        "LOGS_DIR": runtime / "logs",
        "BACKUPS_DIR": runtime / "backups",
        "STATE_DIR": runtime / "state",
        "CLI_HOMES_DIR": runtime / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
    }
    for name, value in paths.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_IDS", [100])
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    db.init()
    return config.DB_PATH


def add_user(uid: int, *, status: str = "pending", role: str = "user") -> sqlite3.Row:
    access.upsert_seen(uid, f"user{uid}", f"User {uid}")
    if status == "approved" and role == "admin":
        access.promote(uid)
    elif status == "approved":
        access.approve(uid)
    elif status == "denied":
        access.deny(uid)
    return access.get_user(uid)


def test_database_fixture_is_isolated(isolated_database: Path) -> None:
    assert isolated_database.name == "bridge.sqlite3"
    assert isolated_database.exists()
    assert isolated_database != Path(__file__).resolve().parents[2] / "bridge.sqlite3"


def test_owner_is_seeded_as_approved_admin(isolated_database: Path) -> None:
    owner = access.get_user(100)

    assert owner["role"] == "admin"
    assert owner["status"] == "approved"
    assert access.is_owner(100)
    assert access.is_admin_id(100)
    assert access.is_allowed_id(100)


def test_default_mode_is_approve(isolated_database: Path) -> None:
    assert access.get_mode() == "approve"


@pytest.mark.parametrize("mode", access.MODES)
def test_access_mode_round_trip(isolated_database: Path, mode: str) -> None:
    access.set_mode(mode)

    assert access.get_mode() == mode


def test_unknown_mode_is_rejected(isolated_database: Path) -> None:
    with pytest.raises(ValueError, match="неизвестный режим"):
        access.set_mode("public")

    assert access.get_mode() == "approve"


def test_unknown_database_mode_falls_back_to_approve(isolated_database: Path) -> None:
    with db.conn() as connection:
        connection.execute("INSERT INTO settings(key, value) VALUES ('access_mode', 'broken')")

    assert access.get_mode() == "approve"


def test_upsert_seen_creates_pending_user(isolated_database: Path) -> None:
    user = access.upsert_seen(200, "new_user", "New User")

    assert user["telegram_id"] == 200
    assert user["username"] == "new_user"
    assert user["first_name"] == "New User"
    assert user["role"] == "user"
    assert user["status"] == "pending"
    assert user["last_seen"] is not None


def test_upsert_seen_does_not_reset_access_decision(isolated_database: Path) -> None:
    add_user(200, status="approved", role="admin")

    updated = access.upsert_seen(200, "renamed", None)

    assert updated["username"] == "renamed"
    assert updated["first_name"] == "User 200"
    assert updated["role"] == "admin"
    assert updated["status"] == "approved"


@pytest.mark.parametrize(
    ("mode", "status", "role", "expected"),
    [
        ("open", "pending", "user", True),
        ("open", "approved", "user", True),
        ("open", "denied", "user", False),
        ("approve", "pending", "user", False),
        ("approve", "approved", "user", True),
        ("approve", "approved", "admin", True),
        ("approve", "denied", "user", False),
        ("admins", "pending", "user", False),
        ("admins", "approved", "user", False),
        ("admins", "approved", "admin", True),
        ("admins", "denied", "user", False),
    ],
)
def test_access_matrix(
    isolated_database: Path, mode: str, status: str, role: str, expected: bool
) -> None:
    add_user(200, status=status, role=role)
    access.set_mode(mode)

    assert access.is_allowed_id(200) is expected


@pytest.mark.parametrize("mode", access.MODES)
def test_owner_is_allowed_in_every_mode(isolated_database: Path, mode: str) -> None:
    access.set_mode(mode)

    assert access.is_allowed_id(100)


def test_unknown_user_is_denied_even_in_open_mode(isolated_database: Path) -> None:
    access.set_mode("open")

    assert not access.is_allowed_id(999)
    assert not access.is_allowed_id(None)


def test_role_actions_have_expected_transitions(isolated_database: Path) -> None:
    add_user(200)

    access.approve(200)
    assert access.get_user(200)["status"] == "approved"
    assert access.get_user(200)["role"] == "user"

    access.promote(200)
    assert access.get_user(200)["status"] == "approved"
    assert access.get_user(200)["role"] == "admin"

    access.demote(200)
    assert access.get_user(200)["status"] == "approved"
    assert access.get_user(200)["role"] == "user"

    access.deny(200)
    assert access.get_user(200)["status"] == "denied"
    assert access.get_user(200)["role"] == "user"


def test_mark_requested_sets_timestamp(isolated_database: Path) -> None:
    add_user(200)

    access.mark_requested(200)

    assert access.get_user(200)["requested_at"] is not None


def test_database_admin_must_also_be_approved(isolated_database: Path) -> None:
    add_user(200, status="approved", role="admin")
    add_user(300, status="approved", role="admin")
    access.deny(300)

    assert access.is_admin_id(200)
    assert not access.is_admin_id(300)
    assert not access.is_admin_id(999)


def test_user_search_matches_id_username_and_name(isolated_database: Path) -> None:
    access.upsert_seen(200, "CreativePerson", "Pavel")
    access.upsert_seen(300, "other", "Ilya")

    assert [row["telegram_id"] for row in access.list_users("creative")] == [200]
    assert [row["telegram_id"] for row in access.list_users("@CREATIVE")] == [200]
    assert [row["telegram_id"] for row in access.list_users("pav")] == [200]
    assert [row["telegram_id"] for row in access.list_users("300")] == [300]
    assert access.count_users("ily") == 1


def test_pending_users_are_listed_before_approved(isolated_database: Path) -> None:
    add_user(200, status="pending")
    add_user(300, status="approved")

    rows = access.list_users()

    assert rows[0]["telegram_id"] == 200


def test_user_badges_and_lines(isolated_database: Path) -> None:
    user = add_user(200, status="approved")
    admin = add_user(300, status="approved", role="admin")
    denied = add_user(400, status="denied")

    assert access.user_badge(access.get_user(100)) == "👑"
    assert access.user_badge(admin) == "⭐"
    assert access.user_badge(user) == "✅"
    assert access.user_badge(denied) == "⛔"
    assert access.user_line(user) == "✅ User 200 @user200 · 200"
