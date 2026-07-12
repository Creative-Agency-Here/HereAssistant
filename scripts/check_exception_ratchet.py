#!/usr/bin/env python3
"""Запрещает рост legacy `except Exception` и broad catches в critical scope."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".runtime",
    ".venv",
    ".venv-next",
    "__pycache__",
    "node_modules",
    "tests",
}

# Зафиксированный debt после аудита 2026-07-11. Уменьшать можно, увеличивать нельзя.
ALLOWANCE = {
    "bot.py": 11,
    "chat.py": 2,
    "core/changes.py": 3,
    "core/version.py": 7,
    "handlers/common.py": 1,
    "handlers/deploy.py": 4,
    "handlers/diff.py": 2,
    "handlers/message_attachments.py": 3,
    "handlers/message_final_delivery.py": 6,
    "handlers/message_live.py": 1,
    "handlers/message_progress_delivery.py": 1,
    "handlers/message_rich_final.py": 2,
    "handlers/messages.py": 6,
    "handlers/projects.py": 1,
    "handlers/system.py": 3,
    "handlers/team.py": 2,
    "providers/claude_code.py": 4,
    "providers/gemini.py": 4,
    "providers/process.py": 2,
    "restart_bot.py": 2,
    "scripts/setup_assistant.py": 1,
    "utils/files.py": 1,
    "utils/memory_link.py": 3,
    "utils/rich.py": 1,
    "utils/single_instance.py": 4,
    "utils/table_render.py": 2,
    "webapp/api/routes/status.py": 1,
    "webapp/api/routes/ws.py": 4,
}

CRITICAL_ZERO = {
    "core/access.py",
    "core/db.py",
    "core/events.py",
    "core/project_config.py",
    "webapp/api/auth.py",
    "webapp/api/routes/tasks.py",
}


def broad_counts() -> dict[str, int]:
    result: dict[str, int] = {}
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if IGNORED_PARTS.intersection(path.relative_to(ROOT).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "Exception"
        )
        if count:
            result[relative] = count
    return result


def main() -> int:
    counts = broad_counts()
    failures: list[str] = []
    for path, count in sorted(counts.items()):
        allowed = ALLOWANCE.get(path, 0)
        if count > allowed:
            failures.append(f"{path}: {count} broad catches, allowed {allowed}")
    for path in sorted(CRITICAL_ZERO):
        if counts.get(path, 0):
            failures.append(f"{path}: critical invariant scope must have zero broad catches")
    stale = sorted(path for path, allowed in ALLOWANCE.items() if counts.get(path, 0) < allowed)
    if stale:
        failures.append("ratchet allowance can be lowered: " + ", ".join(stale))
    if failures:
        print("\n".join(failures))
        return 1
    print(f"exception ratchet: {sum(counts.values())} classified legacy boundary catches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
