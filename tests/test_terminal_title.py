from __future__ import annotations

import asyncio
from io import StringIO

from terminal_title import TerminalTitle, compact_title, task_word


def test_compact_title_and_russian_task_word() -> None:
    assert compact_title("  Очень   важная\nзадача ") == "Очень важная задача"
    assert compact_title("x" * 50, limit=10) == "xxxxxxxxx…"
    assert [task_word(value) for value in (0, 1, 2, 5, 11, 21, 24)] == [
        "задач",
        "задача",
        "задачи",
        "задач",
        "задач",
        "задача",
        "задачи",
    ]


async def test_title_animates_and_marks_unfinished_work() -> None:
    output = StringIO()
    title = TerminalTitle(output, enabled=True)

    title.start("Исправить синхронизацию", 2)
    await asyncio.sleep(0)
    await title.finish(completed=False, cwd="/workspace/project", open_tasks=2)

    rendered = output.getvalue()
    assert "2 · Исправить синхронизацию" in rendered
    assert "✕ 2 · Исправить синхронизацию" in rendered
    assert rendered.count("\033]0;") >= 2
    assert rendered.count("\033]2;") >= 2


async def test_completed_title_keeps_cross_while_crm_tasks_are_open() -> None:
    output = StringIO()
    title = TerminalTitle(output, enabled=True)

    title.start("Один шаг", 1)
    await title.finish(completed=True, cwd="/workspace/project", open_tasks=3)

    assert "✕ 3 · Один шаг" in output.getvalue()
