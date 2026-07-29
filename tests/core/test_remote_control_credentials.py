"""Хранилище device credential: секрет не попадает в SQLite, фолбэк — файл 0600.

Сырой token существует только в OS secret store (или защищённом файле) и в памяти
процесса. Здесь проверяем, что после сохранения credential во ВСЕЙ базе SQLite нет
значения секрета, а файловый фолбэк создаётся с правами 0600.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from core import config, db
from core.remote_control import credential_store
from core.remote_control.credential_store import (
    DeviceCredential,
    FileCredentialStore,
    KeychainCredentialStore,
    default_store,
)

SECRET_TOKEN = "harc_SUPER_SECRET_9f8e7d6c5b4a"


@pytest.fixture
def rc_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Изолированная SQLite: все пути рантайма уводим во временный каталог.
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
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    db.init()
    return config.DB_PATH


def make_credential() -> DeviceCredential:
    return DeviceCredential(
        token=SECRET_TOKEN,
        device_id="device-1",
        scopes=("rc:connect", "rc:claim"),
        fingerprint="fp-123",
        expires_at=1_700_000_000,
    )


def _whole_database_text(db_path: Path) -> str:
    """Сливает всё содержимое каждой таблицы БД в текст — для поиска утечки."""
    chunks: list[str] = []
    with sqlite3.connect(db_path) as connection:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for table in tables:
            for row in connection.execute(f"SELECT * FROM {table}"):
                chunks.extend(str(value) for value in row)
    return "\n".join(chunks)


def test_secret_never_reaches_sqlite(rc_database: Path, tmp_path: Path) -> None:
    path = tmp_path / "rc" / "device_credential.json"
    store = FileCredentialStore(path=path)
    store.save(make_credential())

    # Sanity: секрет действительно сохранён (в файле, не в БД).
    loaded = store.load()
    assert loaded is not None and loaded.token == SECRET_TOKEN
    assert SECRET_TOKEN in path.read_text(encoding="utf-8")

    # Во всей базе SQLite нет ни самого секрета, ни содержимого файла credential.
    dump = _whole_database_text(rc_database)
    assert SECRET_TOKEN not in dump
    assert path.read_text(encoding="utf-8") not in dump


def test_file_fallback_created_with_0600(tmp_path: Path) -> None:
    path = tmp_path / "rc" / "device_credential.json"
    store = FileCredentialStore(path=path)

    assert store.load() is None
    store.save(make_credential())

    assert path.exists()
    # Права строго 0600: владелец читает/пишет, остальные — нет.
    assert (path.stat().st_mode & 0o777) == 0o600

    store.delete()
    assert store.load() is None


def test_keychain_secret_stays_outside_repo_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keychain-хранилище отдаёт секрет OS secret store: в файлах репозитория его нет.
    stored: dict[str, str] = {}

    def fake_security(*args: str) -> subprocess.CompletedProcess:
        if args[0] == "add-generic-password":
            stored["secret"] = args[args.index("-w") + 1]
            return subprocess.CompletedProcess(args, 0, stdout="")
        if args[0] == "find-generic-password":
            if "secret" not in stored:
                raise subprocess.CalledProcessError(44, "security")
            return subprocess.CompletedProcess(args, 0, stdout=stored["secret"] + "\n")
        raise AssertionError(f"неожиданный вызов security: {args}")

    monkeypatch.setattr(credential_store, "_security", fake_security)
    store = KeychainCredentialStore()
    store.save(make_credential())

    # Секрет осел в «секретном сторе», а не в файлах.
    assert SECRET_TOKEN in stored["secret"]
    assert list(tmp_path.iterdir()) == []

    loaded = store.load()
    assert loaded is not None and loaded.token == SECRET_TOKEN


def test_default_store_selection_by_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credential_store.sys, "platform", "darwin")
    assert isinstance(default_store(), KeychainCredentialStore)

    monkeypatch.setattr(credential_store.sys, "platform", "linux")
    assert isinstance(default_store(), FileCredentialStore)
