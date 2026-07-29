"""Кап пагинации нельзя обойти отрицательным limit.

`min(-1, 200)` даёт `-1`, а SQLite понимает `LIMIT -1` как «без ограничения»:
запрос `?limit=-1` возвращал всю таблицу вместе с полными диффами.
"""

from __future__ import annotations

import sqlite3

import pytest

from webapp.api.pagination import MAX_LIMIT, bounded_limit, bounded_offset


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-1", 1),
        ("-999", 1),
        ("0", 1),
        ("1", 1),
        ("50", 50),
        ("200", 200),
        ("1000", MAX_LIMIT),
        (None, 50),
    ],
)
def test_limit_is_always_inside_bounds(raw: object, expected: int) -> None:
    assert bounded_limit(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [("-5", 0), ("0", 0), ("17", 17), (None, 0)])
def test_offset_is_never_negative(raw: object, expected: int) -> None:
    assert bounded_offset(raw) == expected


def test_non_numeric_limit_raises_for_route_to_answer_400() -> None:
    """Роут ловит ValueError и отвечает 400 — глушить его здесь нельзя."""
    with pytest.raises(ValueError):
        bounded_limit("сорок")


def test_sqlite_treats_negative_limit_as_unlimited() -> None:
    """Причина, по которой нижняя граница нужна именно здесь."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE t(x)")
    connection.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(300)])

    rows = connection.execute("SELECT * FROM t LIMIT -1").fetchall()

    assert len(rows) == 300, "SQLite не ограничивает выборку при LIMIT -1"


def test_routes_use_the_shared_helper() -> None:
    """Формула не должна расползтись обратно по роутам."""
    from webapp.api.routes import changes, history

    for module in (changes, history):
        assert module.bounded_limit is bounded_limit
        assert module.bounded_offset is bounded_offset
