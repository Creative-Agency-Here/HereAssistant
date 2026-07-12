"""SQLite repository и typed DTO аккаунтов manager UI."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


class ProviderSpec(TypedDict):
    key: str
    title: str
    subtitle: str
    bin: str
    npm_pkg: str
    env_var: str
    default_model: str
    login_hint: str


@dataclass(frozen=True, slots=True)
class NewAccount:
    provider: str
    label: str
    cli_home_path: Path
    default_model: str
    notes: str = ""
    owner_user_id: int | None = None
    shared: bool = False


class AccountExistsError(ValueError):
    pass


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def list_accounts(db_path: Path) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        return list(connection.execute("SELECT * FROM accounts ORDER BY id"))


def account_by_label(db_path: Path, label: str) -> sqlite3.Row | None:
    with connect(db_path) as connection:
        return connection.execute("SELECT * FROM accounts WHERE label=?", (label,)).fetchone()


def create_account(db_path: Path, account: NewAccount) -> None:
    try:
        with connect(db_path) as connection:
            connection.execute(
                "INSERT INTO accounts(provider,label,cli_home_path,default_model,enabled,notes,"
                "owner_user_id,shared) VALUES (?,?,?,?,1,?,?,?)",
                (
                    account.provider,
                    account.label,
                    str(account.cli_home_path),
                    account.default_model,
                    account.notes,
                    account.owner_user_id,
                    int(account.shared),
                ),
            )
    except sqlite3.IntegrityError as error:
        raise AccountExistsError(account.label) from error


def delete_account(db_path: Path, label: str) -> bool:
    with connect(db_path) as connection:
        cursor = connection.execute("DELETE FROM accounts WHERE label=?", (label,))
    return cursor.rowcount > 0


def update_account_access(
    db_path: Path, label: str, *, owner_user_id: int | None, shared: bool
) -> bool:
    """Назначает владельца либо явный shared-доступ (взаимоисключающие режимы)."""
    if owner_user_id is not None and shared:
        raise ValueError("Аккаунт не может одновременно иметь владельца и быть shared")
    with connect(db_path) as connection:
        cursor = connection.execute(
            "UPDATE accounts SET owner_user_id=?, shared=? WHERE label=?",
            (owner_user_id, int(shared), label),
        )
    return cursor.rowcount > 0


def sanitize_label(label: str) -> str:
    return "".join(character for character in label if character.isalnum() or character in "-_")
