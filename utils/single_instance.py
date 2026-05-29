"""Single-instance guard для bot.py.

При старте пишем свой PID в lock-файл. Если файл уже есть и PID живой —
выходим с человеческим сообщением, не запуская двух ботов одновременно.
"""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from core import config

log = logging.getLogger("bridge.lock")

LOCK_FILE = config.STATE_DIR / "bot.lock"


def _is_pid_alive(pid: int) -> bool:
    """Жив ли процесс с таким PID. Windows-вариант через tasklist."""
    if pid <= 0:
        return False
    try:
        # tasklist выдаёт CSV; ищем точное совпадение PID
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        # если tasklist недоступен — считаем что жив, чтобы не запускать конкурента
        return True


def _read_lock() -> tuple[int, float] | None:
    if not LOCK_FILE.exists():
        return None
    try:
        raw = LOCK_FILE.read_text(encoding="utf-8").strip()
        parts = raw.split("|", 1)
        pid = int(parts[0])
        ts = float(parts[1]) if len(parts) > 1 else 0.0
        return pid, ts
    except Exception:
        return None


def _write_lock():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(f"{os.getpid()}|{time.time():.0f}", encoding="utf-8")


def _remove_lock():
    """Снять лок только если он наш — чтобы не убирать чужой."""
    info = _read_lock()
    if info and info[0] == os.getpid():
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass


def ensure_single_instance():
    """Если в .runtime/state/bot.lock записан живой чужой PID — выходим.
    Иначе занимаем лок и регистрируем уборку при выходе.
    """
    info = _read_lock()
    if info:
        other_pid, other_ts = info
        if other_pid != os.getpid() and _is_pid_alive(other_pid):
            uptime_min = int((time.time() - other_ts) / 60)
            sys.stderr.write(
                "\n" + "=" * 60 + "\n"
                "  Бот уже запущен.\n"
                f"  PID={other_pid}, работает уже {uptime_min} мин.\n"
                f"  Lock-файл: {LOCK_FILE}\n"
                "\n"
                "  Чтобы запустить новый — сначала остановите старый\n"
                f"  (taskkill /PID {other_pid} /F) или удалите lock-файл,\n"
                "  если процесс на самом деле не работает.\n"
                + "=" * 60 + "\n"
            )
            sys.exit(2)
        # stale-лок — перезапишем своим
        log.info("found stale lock pid=%s, taking over", other_pid)

    _write_lock()
    atexit.register(_remove_lock)
    log.info("single-instance lock acquired pid=%s", os.getpid())
