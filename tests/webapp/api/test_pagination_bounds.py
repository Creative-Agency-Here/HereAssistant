"""Кап пагинации нельзя обойти отрицательным limit.

`min(-1, 200)` даёт `-1`, а SQLite понимает `LIMIT -1` как «без ограничения»:
запрос `?limit=-1` возвращал всю таблицу вместе с полными диффами.
"""

from __future__ import annotations

import inspect

import pytest

from webapp.api.routes import changes, history


@pytest.mark.parametrize("module", [changes, history])
def test_limit_has_lower_bound(module: object) -> None:
    source = inspect.getsource(module.list_handler)  # type: ignore[attr-defined]

    assert "max(1, min(" in source, "нижняя граница limit обязательна"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-1", 1), ("0", 1), ("50", 50), ("1000", 200), ("200", 200)],
)
def test_bounds_formula(raw: str, expected: int) -> None:
    """Формула из роутов: значение всегда в диапазоне 1..200."""
    assert max(1, min(int(raw), 200)) == expected


def test_sqlite_treats_negative_limit_as_unlimited() -> None:
    """Причина, по которой нижняя граница нужна именно здесь."""
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE t(x)")
    connection.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(300)])

    rows = connection.execute("SELECT * FROM t LIMIT -1").fetchall()

    assert len(rows) == 300, "SQLite не ограничивает выборку при LIMIT -1"
