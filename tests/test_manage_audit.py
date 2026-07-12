import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import manage_audit
from manage_audit import account_usage, format_tokens, ssh_history, telegram_history


def create_events_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp INTEGER, "
            "event_type TEXT, user_id INTEGER, account_label TEXT, "
            "tokens_in INTEGER, tokens_out INTEGER, payload TEXT)"
        )


def test_account_usage_counts_window_and_recent_rate_limit(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    create_events_db(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, 900, "message_out", 1, "main", 10, 20, "{}"),
                (2, 800, "message_out", 1, "other", 100, 200, "{}"),
                (
                    3,
                    950,
                    "rate_limit",
                    1,
                    "main",
                    0,
                    0,
                    json.dumps({"rate_limit_reset": "12:00"}),
                ),
            ],
        )

    assert account_usage(path, "main", now=1000) == {
        "msgs": 1,
        "tokens": 30,
        "limited": True,
        "reset": "12:00",
    }


def test_account_usage_and_history_fail_closed_without_schema(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite3"

    assert account_usage(path, "main", now=1000) == {
        "msgs": 0,
        "tokens": 0,
        "limited": False,
        "reset": None,
    }
    assert telegram_history(path) == []


def test_malformed_rate_limit_payload_keeps_limited_without_reset(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    create_events_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO events VALUES (1,950,'rate_limit',1,'main',0,0,'{bad')")

    usage = account_usage(path, "main", now=1000)

    assert usage["limited"]
    assert usage["reset"] is None


def test_telegram_history_filters_events_orders_and_bounds(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    create_events_db(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, 100, "message_in", 1, "main", 2, 3, "{}"),
                (2, 200, "unrelated", 1, "main", 4, 5, "{}"),
                (3, 300, "error", None, None, 0, 0, "{}"),
            ],
        )

    entries = telegram_history(path, limit=1)

    assert entries == [
        {
            "timestamp": 300,
            "event_type": "error",
            "user_id": None,
            "account_label": None,
            "tokens": 0,
        }
    ]


def test_ssh_history_filters_footer_and_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        manage_audit.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="alice pts/0 host\nwtmp begins\n"),
    )
    assert ssh_history() == ["alice pts/0 host"]

    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired("last", 5)

    monkeypatch.setattr(manage_audit.subprocess, "run", timeout)
    assert ssh_history() == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [(999, "999"), (1000, "1k"), (1_500_000, "1.5M")],
)
def test_format_tokens(value: int, expected: str) -> None:
    assert format_tokens(value) == expected
