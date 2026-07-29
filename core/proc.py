"""Безопасное завершение дочерних процессов — общее для всех вызывающих.

Один helper на весь проект: и провайдеры CLI, и git-операции, и клиент vault
обязаны гасить свои процессы одинаково. Своя копия этой логики в каждом модуле
уже приводила к тому, что путь отмены забывали (см. providers/base.py и
core/git_projects.py).
"""

from __future__ import annotations

import asyncio


async def cancel_and_reap(proc: asyncio.subprocess.Process, timeout: float = 5) -> None:
    """Немедленно убивает процесс и забирает его статус.

    Нужен на путях timeout и отмены задачи: без reap дочерний процесс остаётся
    сиротой и продолжает работать — писать в каталог проекта, занимать сеть,
    держать блокировки. Операция идемпотентна: уже завершённый процесс не
    трогаем, гонку kill-после-выхода гасим.

    Вызывается в том числе из обработчика ``CancelledError``, поэтому ожидание
    ограничено по времени и наружу ничего не выбрасывает.
    """
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        # Процесс успел завершиться сам между проверкой и kill.
        pass
    try:
        await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Повторная отмена или зависший reap не должны подменять исходную причину.
        pass
