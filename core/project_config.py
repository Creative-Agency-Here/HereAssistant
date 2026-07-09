"""Privacy-first политика проекта: чтение .hereassistant/project.yml.

Принцип: default deny. Нет файла, битый файл, нет PyYAML — проект считается
private: ничего не сохраняем (prompt/result/diff) и ничего не синкаем в CRM.

Режимы:
  private — дефолт; хранение выключено, CRM выключен. Отдельные storage-флаги
            можно явно включить в конфиге (осознанное решение владельца).
  local   — данные можно хранить локально (по storage-флагам), но CRM/service
            API их не видит никогда.
  crm     — явный opt-in: mode: crm + sync.enabled: true + crm_project_id или
            crm_task_id. Наружу уходят только типы данных с явным send_* = true.

Формат .hereassistant/project.yml — см. docs/privacy.md.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("bridge.project_config")

CONFIG_DIR_NAME = ".hereassistant"
CONFIG_FILE_NAME = "project.yml"

# Типы данных, которые можно (потенциально) синкать в CRM — каждый под флагом.
SYNC_DATA_TYPES = ("prompts", "messages", "diffs", "commits", "deploys", "artifacts")

try:  # PyYAML опционален по духу default deny: нет парсера — всё private
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


@dataclass(frozen=True)
class ProjectPolicy:
    mode: str = "private"
    name: Optional[str] = None
    crm_project_id: Optional[str] = None
    crm_task_id: Optional[str] = None
    sync_enabled: bool = False
    # send_prompts/send_messages/... — только явные true из конфига
    sync_flags: dict = field(default_factory=dict)
    save_history: bool = False
    save_messages: bool = False
    save_file_changes: bool = False


# Политика по умолчанию — полный запрет.
PRIVATE = ProjectPolicy()

# Кэш по пути конфига: (mtime, policy). Обновляется при изменении файла.
_cache: dict[str, tuple[float, ProjectPolicy]] = {}
# Отрицательный кэш «файла нет» с TTL, чтобы не дёргать диск на каждое сообщение.
_missing_cache: dict[str, float] = {}
_MISSING_TTL_SEC = 30


def _config_path(cwd: str | Path) -> Path:
    return Path(cwd) / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def _as_bool(v) -> bool:
    """Только явный true включает флаг (строки 'true'/'yes'/'1' тоже принимаем)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def _parse(raw: dict) -> ProjectPolicy:
    mode = str(raw.get("mode", "private")).strip().lower()
    if mode not in ("private", "local", "crm"):
        mode = "private"

    sync = raw.get("sync") or {}
    storage = raw.get("storage") or {}
    if not isinstance(sync, dict):
        sync = {}
    if not isinstance(storage, dict):
        storage = {}

    crm_project_id = raw.get("crm_project_id") or None
    crm_task_id = raw.get("crm_task_id") or None
    # CRM включается только полным набором условий (ТЗ §7).
    sync_enabled = (
        mode == "crm"
        and _as_bool(sync.get("enabled"))
        and bool(crm_project_id or crm_task_id)
    )

    return ProjectPolicy(
        mode=mode,
        name=str(raw.get("name")) if raw.get("name") else None,
        crm_project_id=str(crm_project_id) if crm_project_id else None,
        crm_task_id=str(crm_task_id) if crm_task_id else None,
        sync_enabled=sync_enabled,
        sync_flags={
            f"send_{t}": _as_bool(sync.get(f"send_{t}")) for t in SYNC_DATA_TYPES
        },
        save_history=_as_bool(storage.get("save_history")),
        save_messages=_as_bool(storage.get("save_messages")),
        save_file_changes=_as_bool(storage.get("save_file_changes")),
    )


def policy_for(cwd: str | Path | None) -> ProjectPolicy:
    """Политика проекта по его рабочему каталогу. Любая ошибка → PRIVATE."""
    if not cwd:
        return PRIVATE
    path = _config_path(cwd)
    key = str(path)

    now = time.time()
    if key in _missing_cache and now - _missing_cache[key] < _MISSING_TTL_SEC:
        return PRIVATE

    try:
        stat = path.stat()
    except OSError:
        _missing_cache[key] = now
        return PRIVATE
    _missing_cache.pop(key, None)

    cached = _cache.get(key)
    if cached and cached[0] == stat.st_mtime:
        return cached[1]

    if yaml is None:
        # Без предупреждения на каждый вызов — один раз достаточно.
        if not _cache:
            log.warning("PyYAML не установлен — все проекты считаются private")
        return PRIVATE

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("project.yml: ожидался mapping")
        policy = _parse(raw)
    except Exception as e:
        # Не логируем содержимое файла — только факт и класс ошибки.
        log.warning("project.yml не прочитан (%s) — режим private", type(e).__name__)
        policy = PRIVATE

    _cache[key] = (stat.st_mtime, policy)
    return policy


# ---------- хелперы-гейты (единственная точка принятия решения) ----------

def can_store_history(policy: ProjectPolicy) -> bool:
    """Можно ли вообще вести историю (метаданные диалога)."""
    return policy.save_history


def can_store_messages(policy: ProjectPolicy) -> bool:
    """Можно ли сохранять содержимое сообщений (prompt/result)."""
    return policy.save_messages


def can_store_file_changes(policy: ProjectPolicy) -> bool:
    """Можно ли писать полные диффы правок в журнал."""
    return policy.save_file_changes


def can_sync_to_crm(policy: ProjectPolicy, data_type: str) -> bool:
    """Можно ли отправить данный тип данных в CRM. data_type — из SYNC_DATA_TYPES."""
    if policy.mode != "crm" or not policy.sync_enabled:
        return False
    return bool(policy.sync_flags.get(f"send_{data_type}"))


def is_crm_visible(policy: ProjectPolicy) -> bool:
    """Виден ли проект CRM/service API вообще (private/local — никогда)."""
    return policy.mode == "crm" and policy.sync_enabled
