"""Хранилище device credential для /rc.

Секрет устройства НЕЛЬЗЯ хранить в SQLite, .env или project.yml. Здесь —
абстракция хранилища с двумя реализациями:

* macOS Keychain через ``security add-generic-password``/``find-generic-password``;
* фолбэк в отдельный файл с правами 0600 и явным предупреждением в лог.

Сырой credential существует только в OS secret store (или защищённом файле) и в
памяти процесса; в логи и БД попадает лишь факт наличия/отсутствия секрета.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from .. import config

log = logging.getLogger("bridge.remote_control.credential")

_KEYCHAIN_SERVICE = "HereAssistant Remote Control"
_KEYCHAIN_ACCOUNT = "device-credential"


class CredentialStoreError(RuntimeError):
    """Безопасная ошибка хранилища секрета (без тела секрета в сообщении)."""


@dataclass(frozen=True, slots=True)
class DeviceCredential:
    """Минимальный набор полей device credential. Секрет — только ``token``."""

    token: str
    device_id: str
    scopes: tuple[str, ...] = ()
    fingerprint: Optional[str] = None
    expires_at: Optional[int] = None
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        payload = asdict(self)
        payload["scopes"] = list(self.scopes)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "DeviceCredential":
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("token") or not data.get("device_id"):
            raise CredentialStoreError("credential_invalid")
        scopes = data.get("scopes") or ()
        return cls(
            token=str(data["token"]),
            device_id=str(data["device_id"]),
            scopes=tuple(str(s) for s in scopes),
            fingerprint=data.get("fingerprint"),
            expires_at=data.get("expires_at"),
            extra=data.get("extra") or {},
        )


class CredentialStore(Protocol):
    """Интерфейс хранилища секрета устройства."""

    backend: str

    def load(self) -> Optional[DeviceCredential]:
        ...

    def save(self, credential: DeviceCredential) -> None:
        ...

    def delete(self) -> None:
        ...


def _security(*args: str) -> subprocess.CompletedProcess:
    """Тонкая обёртка над ``security`` — удобно подменять в тестах."""
    return subprocess.run(
        ("security", *args),
        capture_output=True,
        text=True,
        check=True,
    )


class KeychainCredentialStore:
    """macOS Keychain через CLI ``security``. Секрет не попадает в файлы repo."""

    backend = "macos-keychain"

    def __init__(self, service: str = _KEYCHAIN_SERVICE, account: str = _KEYCHAIN_ACCOUNT) -> None:
        self._service = service
        self._account = account

    def load(self) -> Optional[DeviceCredential]:
        try:
            result = _security(
                "find-generic-password", "-a", self._account, "-s", self._service, "-w"
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        secret = result.stdout.strip()
        if not secret:
            return None
        try:
            return DeviceCredential.from_json(secret)
        except (CredentialStoreError, ValueError) as error:
            log.warning("Keychain credential не прочитан (%s)", type(error).__name__)
            return None

    def save(self, credential: DeviceCredential) -> None:
        try:
            _security(
                "add-generic-password",
                "-a",
                self._account,
                "-s",
                self._service,
                "-w",
                credential.to_json(),
                "-U",
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            raise CredentialStoreError("keychain_unavailable") from error

    def delete(self) -> None:
        try:
            _security("delete-generic-password", "-a", self._account, "-s", self._service)
        except subprocess.CalledProcessError:
            # Отсутствие записи — не ошибка удаления.
            return
        except FileNotFoundError as error:
            raise CredentialStoreError("keychain_unavailable") from error


class FileCredentialStore:
    """Фолбэк: отдельный файл 0600. Используется только когда нет OS secret store."""

    backend = "file-0600"

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (config.STATE_DIR / "remote_control" / "device_credential.json")
        self._warned = False

    def _warn_once(self) -> None:
        if self._warned:
            return
        self._warned = True
        log.warning(
            "OS secret store недоступен — device credential хранится в файле 0600: %s. "
            "Это менее безопасно, чем Keychain/Secret Service.",
            self._path,
        )

    def load(self) -> Optional[DeviceCredential]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return DeviceCredential.from_json(raw)
        except (CredentialStoreError, ValueError) as error:
            log.warning("Файл credential не прочитан (%s)", type(error).__name__)
            return None

    def save(self, credential: DeviceCredential) -> None:
        self._warn_once()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Пишем атомарно и сразу ограничиваем права: секрет не должен быть
        # доступен другим пользователям даже на время записи.
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, credential.to_json().encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(self._path, 0o600)

    def delete(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            return


def default_store() -> CredentialStore:
    """Keychain на macOS, иначе файловый фолбэк 0600."""
    if sys.platform == "darwin":
        return KeychainCredentialStore()
    return FileCredentialStore()
