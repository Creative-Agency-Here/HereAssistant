"""Единый экран настройки native AI-сессий и HereCRM."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from core import db, native_hooks, native_sessions, project_config
from manage_ui import B, D, G, M, R, X, Y, press_any_key

_STATE_LABELS = {
    "current": f"{G}подключён{X}",
    "disabled": f"{D}не подключён{X}",
    "outdated": f"{Y}нужно обновить{X}",
    "invalid": f"{R}ошибка JSON{X}",
}


def show_native_status() -> None:
    db.init()
    status = native_sessions.connector_status()
    print(f"\n{B}{M}AI-сессии → HereAssistant → HereCRM{X}")
    connector = f"{G}готов{X}" if status["configured"] else f"{R}не настроен{X}"
    user = f"{G}настроен{X}" if status["nativeUserConfigured"] else f"{R}не настроен{X}"
    print(f"  HereCRM connector  {connector}")
    print(f"  Native user       {user}")
    print(f"  Outbox            {status['pending']} ожидает")
    print(f"\n{B}Клиенты{X}")
    for hook in native_hooks.inspect():
        cli = f"{G}CLI найден{X}" if hook.cli_found else f"{D}CLI не найден{X}"
        print(f"  {hook.title:<12} {_STATE_LABELS[hook.state]}, {cli}")
    print(
        f"\n{D}Без .hereassistant/project.yml папка всегда private: "
        f"ни метаданные, ни текст в CRM не уходят.{X}"
    )


def _read_project_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("project.yml должен содержать YAML mapping")
    return data


def _backup_project_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    digest = hashlib.sha256(str(path.parent.parent).encode()).hexdigest()[:12]
    backup_dir = Path.home() / ".hereassistant" / "project-backups" / digest
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = backup_dir / f"project.{stamp}.yml"
    shutil.copy2(path, target)
    target.chmod(0o600)
    return target


def _write_project_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".project.", suffix=".yml", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
        temp.replace(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        temp.unlink(missing_ok=True)
        raise


def _disabled_sync(current: object) -> dict[str, Any]:
    sync = dict(current) if isinstance(current, dict) else {}
    sync["enabled"] = False
    for data_type in project_config.SYNC_DATA_TYPES:
        sync[f"send_{data_type}"] = False
    return sync


def configure_project_interactive() -> None:
    raw_root = input(f"\n{B}Абсолютный путь к папке проекта{X}: ").strip()
    try:
        root = Path(raw_root).expanduser().resolve(strict=True)
    except OSError:
        print(f"{R}Папка не найдена.{X}")
        return
    if not root.is_dir():
        print(f"{R}Нужна именно папка.{X}")
        return
    path = root / project_config.CONFIG_DIR_NAME / project_config.CONFIG_FILE_NAME
    try:
        data = _read_project_config(path)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        print(f"{R}Нельзя безопасно изменить текущий project.yml: {error}{X}")
        return

    print(f"\n  {B}[1]{X} private  {D}ничего не отправлять{X}")
    print(f"  {B}[2]{X} local    {D}только локальные возможности{X}")
    print(f"  {B}[3]{X} crm      {D}явно разрешить синхронизацию{X}")
    choice = input("Режим [1/2/3]: ").strip()
    modes = {"1": "private", "2": "local", "3": "crm"}
    if choice not in modes:
        print("Отмена.")
        return
    mode = modes[choice]
    data["mode"] = mode
    data.setdefault("name", root.name)

    if mode != "crm":
        data["sync"] = _disabled_sync(data.get("sync"))
    else:
        project_id = input("HereCRM project UUID (Enter, если будет task UUID): ").strip()
        task_id = input("HereCRM task UUID (Enter, если указан project): ").strip()
        if not project_id and not task_id:
            print(f"{R}Для crm нужен project UUID или task UUID. Отмена.{X}")
            return
        if project_id:
            data["crm_project_id"] = project_id
        else:
            data.pop("crm_project_id", None)
        if task_id:
            data["crm_task_id"] = task_id
        else:
            data.pop("crm_task_id", None)
        content = input("Отправлять текст prompt/ответов? [y/N]: ").strip().lower()
        send_content = content in ("y", "yes", "д", "да")
        data["sync"] = {
            "enabled": True,
            "send_prompts": send_content,
            "send_messages": send_content,
            "send_diffs": False,
            "send_commits": False,
            "send_deploys": False,
            "send_artifacts": False,
        }

    print(f"\n{B}Будет записано:{X} {path}")
    print(f"  Режим: {mode}")
    if mode == "crm":
        sends = data["sync"]["send_prompts"]
        print(f"  Содержимое: {'prompt и ответы' if sends else 'только метаданные'}")
    confirm = input("Применить? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes", "д", "да"):
        print("Отмена.")
        return
    try:
        _backup_project_config(path)
        _write_project_config(path, data)
        project_config.clear_cache()
    except OSError as error:
        print(f"{R}Не удалось записать project.yml: {error}{X}")
        return
    print(f"{G}✓ Политика папки обновлена.{X}")


def _change_hooks(enabled: bool) -> None:
    try:
        changed = native_hooks.install() if enabled else native_hooks.uninstall()
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
        print(f"{R}Не удалось изменить hooks: {error}{X}")
        return
    for provider, was_changed in changed.items():
        title = native_hooks.CLIENTS[provider].title
        label = "обновлён" if was_changed else "без изменений"
        print(f"  {title}: {label}")


def native_sessions_menu() -> None:
    while True:
        show_native_status()
        print(f"\n  {B}[1]{X} Установить/обновить hooks всех четырёх CLI")
        print(f"  {B}[2]{X} Настроить папку проекта")
        print(f"  {B}[3]{X} Удалить только hooks HereAssistant")
        print(f"  {B}[0]{X} Назад")
        choice = input("\n› ").strip()
        if choice == "1":
            _change_hooks(True)
        elif choice == "2":
            configure_project_interactive()
        elif choice == "3":
            _change_hooks(False)
        elif choice in ("0", "q", "Q"):
            return
        else:
            print(f"{R}Не понял.{X}")
        press_any_key()
