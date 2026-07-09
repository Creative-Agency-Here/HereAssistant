"""Объединение auto-memory всех CLI-аккаунтов в одну папку HereAssistant\\memory.

Для каждого .runtime/cli_homes/<account>/projects/<cwd_slug>/memory/:
  - если уже junction на общую папку — пропуск
  - если обычная папка с файлами — сливает новые/уникальные файлы в общую,
    удаляет себя и заменяется junction'ом
  - если папки нет — создаёт junction
"""

import logging
import os
import shutil
from pathlib import Path

from core import config

log = logging.getLogger("bridge.memory_link")

SHARED_MEMORY = config.BASE_DIR / "memory"


def _is_junction(p: Path) -> bool:
    """True если p — симлинк (POSIX) или NTFS junction/symlink (Windows)."""
    if not p.exists() and not p.is_symlink():
        return False
    if os.name != "nt":
        return p.is_symlink()
    try:
        # На Windows reparse-point = FILE_ATTRIBUTE_REPARSE_POINT (0x400)
        attrs = p.lstat().st_file_attributes if hasattr(p.lstat(), "st_file_attributes") else 0
        return bool(attrs & 0x400)
    except Exception:
        return p.is_symlink()


def _merge_into_shared(src_dir: Path, shared: Path) -> None:
    """Скопировать в shared/ файлы из src_dir, которых там нет (не перезаписывая)."""
    for item in src_dir.iterdir():
        if not item.is_file():
            continue
        target = shared / item.name
        if target.exists():
            continue
        try:
            shutil.copy2(item, target)
            log.info("memory merge: %s -> shared", item.name)
        except Exception as e:
            log.warning("merge failed for %s: %s", item, e)


def _make_junction(link: Path, target: Path) -> bool:
    """Каталожная ссылка: POSIX — symlink, Windows — NTFS junction. True при успехе."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except OSError as e:
            log.warning("symlink failed for %s: %s", link, e)
            return False
    # cmd mklink /J <link> <target> — junction для каталогов, без админ-прав
    rc = os.system(f'cmd /c mklink /J "{link}" "{target}" >nul 2>&1')
    return rc == 0


def ensure_memory_links() -> dict:
    """Пройти по всем cli_homes и связать memory с общей папкой.

    Возвращает {'created': N, 'kept': N, 'merged': N, 'errors': N}.
    """
    SHARED_MEMORY.mkdir(parents=True, exist_ok=True)
    stats = {"created": 0, "kept": 0, "merged": 0, "errors": 0}

    cli_root = config.CLI_HOMES_DIR
    if not cli_root.exists():
        return stats

    for account_dir in cli_root.iterdir():
        if not account_dir.is_dir():
            continue
        projects_dir = account_dir / "projects"
        if not projects_dir.exists():
            continue
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            memory_dir = proj_dir / "memory"

            try:
                if _is_junction(memory_dir):
                    # junction уже есть — проверим что таргет правильный
                    try:
                        tgt = Path(os.readlink(memory_dir)).resolve()
                    except OSError:
                        tgt = None
                    if tgt and tgt == SHARED_MEMORY.resolve():
                        stats["kept"] += 1
                        continue
                    # неправильный таргет — пересоздадим
                    memory_dir.unlink()

                if memory_dir.exists():
                    # обычная папка — слить файлы в общую и удалить
                    _merge_into_shared(memory_dir, SHARED_MEMORY)
                    shutil.rmtree(memory_dir)
                    stats["merged"] += 1

                if _make_junction(memory_dir, SHARED_MEMORY):
                    stats["created"] += 1
                else:
                    stats["errors"] += 1
                    log.warning("mklink /J failed for %s", memory_dir)
            except Exception as e:
                stats["errors"] += 1
                log.warning("link failed for %s: %s", memory_dir, e)

    return stats
