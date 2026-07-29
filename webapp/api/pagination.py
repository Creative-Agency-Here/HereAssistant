"""Границы пагинации — одно место на все роуты.

Формула жила копией в каждом роуте, и в обеих копиях не было нижней границы:
`min(-1, 200)` даёт `-1`, а SQLite понимает `LIMIT -1` как «без ограничения»,
поэтому `?limit=-1` возвращал таблицу целиком вместе с полными диффами.
"""

from __future__ import annotations

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MIN_LIMIT = 1


def bounded_limit(raw: object, *, default: int = DEFAULT_LIMIT) -> int:
    """Приводит запрошенный limit к диапазону 1..200.

    Нечисловое значение — ошибка вызывающего кода (роут отвечает 400),
    поэтому ValueError наружу не глушится.
    """
    value = default if raw is None else int(str(raw))
    return max(MIN_LIMIT, min(value, MAX_LIMIT))


def bounded_offset(raw: object) -> int:
    """Отрицательный offset означает начало выборки, а не ошибку."""
    value = 0 if raw is None else int(str(raw))
    return max(0, value)
