"""Чистые функции подготовки превью, подписи и фильтра правок."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence


def make_preview(markdown: str, limit: int) -> str:
    """Обрезает длинный ответ по последнему естественному разделителю."""
    if len(markdown) <= limit:
        return markdown
    cut = markdown[:limit]
    for separator in ("\n\n", "\n", ". ", " "):
        index = cut.rfind(separator)
        if index >= limit // 2:
            return cut[:index].rstrip() + "\n\n…"
    return cut + "…"


def format_signature(
    model: str | None,
    duration_s: float,
    edits: Sequence[Mapping[str, object]],
    *,
    updated_at: str | None = None,
) -> str:
    """Формирует компактную подпись с агрегированными изменениями по файлам."""
    parts: list[str] = []
    if model:
        parts.append(model)
    parts.append(f"{duration_s:.1f}с")
    if edits:
        total_added = sum(_integer(edit.get("added")) for edit in edits)
        total_removed = sum(_integer(edit.get("removed")) for edit in edits)
        if total_added or total_removed:
            parts.append(f"всего +{total_added} −{total_removed} строк")

        aggregated: dict[str, list[int]] = {}
        for edit in edits:
            path = str(edit.get("file") or "?")
            name = os.path.basename(path.rstrip("/\\")) or "?"
            current = aggregated.setdefault(name, [0, 0])
            current[0] += _integer(edit.get("added"))
            current[1] += _integer(edit.get("removed"))
        count = len(aggregated)
        word = "файл" if count == 1 else ("файла" if 2 <= count <= 4 else "файлов")
        per_file = [f"{name} +{added}/−{removed}" for name, (added, removed) in aggregated.items()]
        if count <= 4:
            parts.append(f"{count} {word}: " + ", ".join(per_file))
        else:
            parts.append(f"{count} {word}: " + ", ".join(per_file[:4]) + f" +ещё {count - 4}")
    parts.append(f"обновлено {updated_at or time.strftime('%H:%M:%S')}")
    return "\n\n— " + " · ".join(parts)


def should_skip_edit(file_path: object) -> bool:
    """Не допускает runtime/secret/temp-файлы в журнал изменений."""
    normalized = str(file_path or "").replace("\\", "/").lower()
    return (
        ".runtime" in normalized
        or "/temp/" in normalized
        or "appdata/local/temp" in normalized
        or "askpass" in normalized
        or "/tmp/" in normalized
        or normalized.endswith(".env")
    )


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
