"""Версия кода: SHA256 bot.py, diff между версиями, бэкапы."""

import datetime
import difflib
import hashlib
import json
import shutil
import time
from pathlib import Path

from . import config

SNAPSHOT_FILE = None  # инициализируется ниже после import config


def file_hash(path: Path) -> str:
    if not path.exists():
        return "0" * 64
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def short(h: str) -> str:
    return h[:6] if h else "------"


def bot_version() -> dict:
    h = file_hash(config.BOT_FILE)
    if config.BOT_FILE.exists():
        mtime = datetime.datetime.fromtimestamp(config.BOT_FILE.stat().st_mtime)
        mtime_str = mtime.strftime("%Y-%m-%d %H:%M")
    else:
        mtime_str = "—"
    return {"hash": h, "short": short(h), "mtime": mtime_str}


# Папки, которые включаем в project-hash. Исключаем .runtime, __pycache__, workspace, backups.
_PROJECT_GLOBS = [
    "bot.py",
    "manage.py",
    "core/**/*.py",
    "handlers/**/*.py",
    "providers/**/*.py",
    "utils/**/*.py",
    "restart_bot.py",
]


def project_files() -> list[Path]:
    files: list[Path] = []
    for pat in _PROJECT_GLOBS:
        for p in sorted(config.BASE_DIR.glob(pat)):
            if "__pycache__" in p.parts or ".runtime" in p.parts:
                continue
            if p.is_file():
                files.append(p)
    return files


def project_version() -> dict:
    """Хеш и mtime всего проекта (а не только bot.py)."""
    files = project_files()
    h = hashlib.sha256()
    latest_mtime = 0.0
    for p in files:
        try:
            h.update(p.read_bytes())
        except Exception:
            continue
        try:
            mt = p.stat().st_mtime
            if mt > latest_mtime:
                latest_mtime = mt
        except Exception:
            pass
    digest = h.hexdigest()
    if latest_mtime:
        mtime_str = datetime.datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M")
    else:
        mtime_str = "—"
    return {"hash": digest, "short": short(digest), "mtime": mtime_str, "files": len(files)}


def _snapshot_path() -> Path:
    return config.STATE_DIR / "snapshot.json"


def current_snapshot() -> dict:
    """Снимок: {относительный путь: {hash, lines}} + общий project_hash."""
    files = project_files()
    snap = {"files": {}, "project_hash": ""}
    h_total = hashlib.sha256()
    for p in files:
        try:
            data = p.read_bytes()
        except Exception:
            continue
        h_total.update(data)
        try:
            rel = str(p.relative_to(config.BASE_DIR)).replace("\\", "/")
        except Exception:
            rel = str(p)
        file_hash_v = hashlib.sha256(data).hexdigest()
        try:
            lines = data.decode("utf-8", errors="replace").splitlines()
        except Exception:
            lines = []
        snap["files"][rel] = {"hash": file_hash_v, "text": "\n".join(lines)}
    snap["project_hash"] = h_total.hexdigest()
    return snap


def save_snapshot() -> None:
    """Сохранить текущее состояние проекта в .runtime/state/snapshot.json."""
    snap = current_snapshot()
    # text каждого файла отдельно не нужен в постоянном снимке — он съест место.
    # Сохраним только hash + lines_count + size, а текст переиспользуем через backup snapshot.
    light = {"project_hash": snap["project_hash"], "files": {}}
    for rel, info in snap["files"].items():
        light["files"][rel] = {
            "hash": info["hash"],
            "lines": len(info["text"].splitlines()) if info["text"] else 0,
        }
    p = _snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # для diff между перезапусками — отдельный файл с полным текстом
    full_path = config.STATE_DIR / "snapshot_full.json"
    full_path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    p.write_text(json.dumps(light, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot_full() -> dict | None:
    p = config.STATE_DIR / "snapshot_full.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def project_changes(old_snap: dict | None) -> list[dict]:
    """Сравнить старый снимок с текущим состоянием. Вернуть список изменений."""
    if not old_snap:
        return []
    old_files = old_snap.get("files", {})
    cur = current_snapshot()
    new_files = cur["files"]
    changes = []
    all_keys = set(old_files) | set(new_files)
    for key in sorted(all_keys):
        old = old_files.get(key)
        new = new_files.get(key)
        if old and not new:
            changes.append(
                {
                    "file": key,
                    "kind": "removed",
                    "added": 0,
                    "removed": len((old.get("text") or "").splitlines()),
                }
            )
            continue
        if new and not old:
            changes.append(
                {
                    "file": key,
                    "kind": "added",
                    "added": len((new.get("text") or "").splitlines()),
                    "removed": 0,
                }
            )
            continue
        if (old or {}).get("hash") == (new or {}).get("hash"):
            continue
        d = diff_stats((old or {}).get("text", ""), (new or {}).get("text", ""))
        changes.append(
            {"file": key, "kind": "modified", "added": d["added"], "removed": d["removed"]}
        )
    return changes


def backup_current_bot() -> Path | None:
    """Скопировать текущий bot.py в .runtime/backups/, вернуть путь."""
    if not config.BOT_FILE.exists():
        return None
    ts = time.strftime("%Y-%m-%d-%H%M%S")
    h = file_hash(config.BOT_FILE)
    target = config.BACKUPS_DIR / f"bot-{ts}-{h[:8]}.py"
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config.BOT_FILE, target)
    # ротация
    backups = sorted(
        config.BACKUPS_DIR.glob("bot-*.py"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in backups[config.BACKUP_RETENTION_COUNT :]:
        try:
            old.unlink()
        except Exception:
            pass
    return target


def diff_stats(old_text: str, new_text: str) -> dict:
    """Считает +N -M строк между двумя версиями."""
    diff = difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="")
    added = 0
    removed = 0
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"added": added, "removed": removed}
