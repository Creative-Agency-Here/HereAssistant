import sqlite3
from pathlib import Path

import pytest

from manage_accounts import (
    AccountExistsError,
    NewAccount,
    account_by_label,
    create_account,
    delete_account,
    list_accounts,
    sanitize_label,
    update_account_access,
)


def create_schema(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, "
            "label TEXT UNIQUE, cli_home_path TEXT, default_model TEXT, enabled INTEGER, "
            "notes TEXT, owner_user_id INTEGER, shared INTEGER NOT NULL DEFAULT 0)"
        )


def account(tmp_path: Path, label: str = "main") -> NewAccount:
    return NewAccount(
        provider="claude_code",
        label=label,
        cli_home_path=tmp_path / label,
        default_model="model-x",
        notes="team",
        owner_user_id=42,
        shared=False,
    )


def test_account_crud_preserves_all_fields(tmp_path: Path) -> None:
    path = tmp_path / "accounts.sqlite3"
    create_schema(path)

    create_account(path, account(tmp_path))

    row = account_by_label(path, "main")
    assert row is not None
    assert dict(row) == {
        "id": 1,
        "provider": "claude_code",
        "label": "main",
        "cli_home_path": str(tmp_path / "main"),
        "default_model": "model-x",
        "enabled": 1,
        "notes": "team",
        "owner_user_id": 42,
        "shared": 0,
    }
    assert [item["label"] for item in list_accounts(path)] == ["main"]
    assert delete_account(path, "main")
    assert not delete_account(path, "main")
    assert account_by_label(path, "main") is None


def test_duplicate_label_raises_domain_error(tmp_path: Path) -> None:
    path = tmp_path / "accounts.sqlite3"
    create_schema(path)
    create_account(path, account(tmp_path))

    with pytest.raises(AccountExistsError, match="main"):
        create_account(path, account(tmp_path))


def test_account_access_requires_explicit_owner_or_shared(tmp_path: Path) -> None:
    path = tmp_path / "accounts.sqlite3"
    create_schema(path)
    create_account(path, account(tmp_path))

    assert update_account_access(path, "main", owner_user_id=None, shared=False)
    row = account_by_label(path, "main")
    assert row is not None and row["owner_user_id"] is None and row["shared"] == 0

    assert update_account_access(path, "main", owner_user_id=None, shared=True)
    row = account_by_label(path, "main")
    assert row is not None and row["shared"] == 1

    with pytest.raises(ValueError, match="одновременно"):
        update_account_access(path, "main", owner_user_id=42, shared=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("main-work_2", "main-work_2"),
        ("bad label!", "badlabel"),
        ("аккаунт-1", "аккаунт-1"),
        ("../", ""),
    ],
)
def test_sanitize_label_keeps_only_supported_characters(raw: str, expected: str) -> None:
    assert sanitize_label(raw) == expected
