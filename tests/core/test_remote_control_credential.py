"""Хранилище device credential: Keychain + файловый фолбэк 0600, секрет не в БД/.env."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from core.remote_control import credential_store
from core.remote_control.credential_store import (
    CredentialStoreError,
    DeviceCredential,
    FileCredentialStore,
    KeychainCredentialStore,
    default_store,
)


def make_credential() -> DeviceCredential:
    return DeviceCredential(
        token="harc_SECRET_TOKEN",
        device_id="device-1",
        scopes=("rc:connect", "rc:claim", "rc:status"),
        fingerprint="fp-123",
        expires_at=1_700_000_000,
    )


def test_credential_json_roundtrip_preserves_secret() -> None:
    credential = make_credential()
    restored = DeviceCredential.from_json(credential.to_json())
    assert restored == credential
    assert restored.scopes == ("rc:connect", "rc:claim", "rc:status")


def test_credential_from_json_rejects_invalid() -> None:
    with pytest.raises(CredentialStoreError):
        DeviceCredential.from_json("{}")
    with pytest.raises(CredentialStoreError):
        DeviceCredential.from_json('{"token": "x"}')


def test_file_store_roundtrip_and_0600_perms(tmp_path: Path) -> None:
    path = tmp_path / "remote_control" / "device_credential.json"
    store = FileCredentialStore(path=path)

    assert store.load() is None
    store.save(make_credential())

    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600

    loaded = store.load()
    assert loaded is not None
    assert loaded.token == "harc_SECRET_TOKEN"

    store.delete()
    assert store.load() is None


def test_file_store_warns_explicitly_about_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = FileCredentialStore(path=tmp_path / "cred.json")
    with caplog.at_level(logging.WARNING, logger="bridge.remote_control.credential"):
        store.save(make_credential())
    assert "0600" in caplog.text


def test_keychain_store_load_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: str) -> subprocess.CompletedProcess:
        raise subprocess.CalledProcessError(44, "security")

    monkeypatch.setattr(credential_store, "_security", missing)
    assert KeychainCredentialStore().load() is None


def test_keychain_store_save_and_load_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
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
    loaded = store.load()
    assert loaded is not None
    assert loaded.token == "harc_SECRET_TOKEN"
    assert loaded.device_id == "device-1"


def test_keychain_store_save_wraps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: str) -> subprocess.CompletedProcess:
        raise subprocess.CalledProcessError(1, "security")

    monkeypatch.setattr(credential_store, "_security", broken)
    with pytest.raises(CredentialStoreError):
        KeychainCredentialStore().save(make_credential())


def test_default_store_prefers_keychain_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credential_store.sys, "platform", "darwin")
    assert isinstance(default_store(), KeychainCredentialStore)

    monkeypatch.setattr(credential_store.sys, "platform", "linux")
    assert isinstance(default_store(), FileCredentialStore)


def test_default_store_is_keychain_on_this_machine() -> None:
    # Тестовая машина — macOS; проверяем фактический выбор без monkeypatch.
    if sys.platform == "darwin":
        assert isinstance(default_store(), KeychainCredentialStore)
    else:
        assert isinstance(default_store(), FileCredentialStore)
