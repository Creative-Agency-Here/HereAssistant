"""Per-thread asyncio-локи: одновременные запросы в один тред — последовательно."""

import asyncio

_locks: dict[tuple[int, int], asyncio.Lock] = {}


def for_thread(chat_id: int, thread_id: int) -> asyncio.Lock:
    key = (chat_id, thread_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock
