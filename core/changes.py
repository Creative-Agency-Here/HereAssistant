"""Журнал изменений файлов: полный дифф каждой правки.

Слой «кто/зачем» — фиксирует правки, прошедшие ЧЕРЕЗ бота:
куда (file), чем (tool), когда (ts), в каком треде/аккаунте/модели и сам дифф.
Правки в обход бота (прямой CLI, руками) этот слой НЕ ловит — для них git.

Хранит в двух местах:
  • таблица file_changes в bridge.sqlite3 — для запросов из вебапа;
  • .runtime/logs/changes/YYYY-MM-DD.md — человекочитаемо, листать в терминале.
"""

import difflib
import logging
import re
import sqlite3
import time
from datetime import datetime

from core import config

log = logging.getLogger("bridge.changes")

# Обрезка old/new для хранения в events.payload (журнал хранит полный текст).
EDIT_SNIPPET_LIMIT = 2000


_HUNK_RE = re.compile(r"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@(.*)$")


def _file_offset(path: str, new: str) -> int:
    """1-based номер строки, где в ТЕКУЩЕМ файле начинается new-фрагмент.
    Нужен, чтобы номера строк в диффе были реальные, а не от 1. Если не нашли — 1."""
    if not new:
        return 1
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return 1
    idx = content.find(new)
    if idx < 0:  # файл мог быть изменён дальше — пробуем по первой строке фрагмента
        first = new.split("\n", 1)[0]
        idx = content.find(first) if first else -1
        if idx < 0:
            return 1
    return content.count("\n", 0, idx) + 1


def _shift_hunk(line: str, delta: int) -> str:
    """Сдвинуть номера в заголовке @@ на delta (чтобы были реальные строки файла)."""
    m = _HUNK_RE.match(line)
    if not m:
        return line
    a = int(m.group(1)) + delta
    c = int(m.group(3)) + delta
    return f"@@ -{a}{m.group(2) or ''} +{c}{m.group(4) or ''} @@{m.group(5)}"


def _unified(file: str, old: str, new: str) -> str:
    """Unified-дифф old→new с РЕАЛЬНЫМИ номерами строк файла (по позиции фрагмента)."""
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{file}", tofile=f"b/{file}", lineterm="",
    ))
    delta = _file_offset(file, new) - 1
    if delta > 0:
        diff = [_shift_hunk(ln, delta) for ln in diff]
    return "\n".join(diff)


def trim_edits_for_events(edits: list) -> list:
    """Копия списка правок с обрезанными old/new — чтобы events.payload не разрастался."""
    out = []
    for e in edits or []:
        e2 = dict(e)
        for k in ("old", "new"):
            v = e2.get(k) or ""
            if len(v) > EDIT_SNIPPET_LIMIT:
                e2[k] = v[:EDIT_SNIPPET_LIMIT] + "\n…[обрезано]"
        out.append(e2)
    return out


def record_edits(thread_id, account, model, edits) -> None:
    """Пишет полный дифф каждой правки в file_changes + дописывает .md по дням.
    Никогда не падает наружу — журнал не должен ронять обработку сообщения."""
    edits = edits or []
    if not edits:
        return
    ts = int(time.time())
    rows = []
    for e in edits:
        file = e.get("file") or "?"
        tool = e.get("tool") or "?"
        old = e.get("old") or ""
        new = e.get("new") or ""
        rows.append((ts, thread_id, account, model, file, tool,
                     e.get("added", 0), e.get("removed", 0),
                     _unified(file, old, new)))

    # --- БД ---
    try:
        c = sqlite3.connect(config.DB_PATH)
        try:
            c.executemany(
                "INSERT INTO file_changes "
                "(ts, thread_id, account, model, file, tool, added, removed, diff) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)
            c.commit()
        finally:
            c.close()
    except Exception as exc:
        log.warning("file_changes insert failed: %s", exc)

    # --- .md по дням ---
    try:
        dt = datetime.fromtimestamp(ts)
        log_dir = config.LOGS_DIR / "changes"
        log_dir.mkdir(parents=True, exist_ok=True)
        md = log_dir / f"{dt:%Y-%m-%d}.md"
        with md.open("a", encoding="utf-8") as f:
            for (_, tid, acc, mdl, file, tool, added, removed, diff) in rows:
                f.write(f"\n## {dt:%H:%M:%S} · тред {tid} · {acc} · {mdl}\n")
                f.write(f"**{tool}** `{file}`  (+{added}/−{removed})\n\n")
                f.write("```diff\n" + diff + "\n```\n")
    except Exception as exc:
        log.warning("file_changes .md append failed: %s", exc)
