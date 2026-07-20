from __future__ import annotations

from pathlib import Path

import pytest

from core import config, contours, control, db, integration_state


@pytest.fixture
def control_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / ".runtime"
    for name, value in {
        "RUNTIME_DIR": runtime,
        "DOWNLOADS_DIR": runtime / "downloads",
        "LOGS_DIR": runtime / "logs",
        "BACKUPS_DIR": runtime / "backups",
        "STATE_DIR": runtime / "state",
        "CLI_HOMES_DIR": runtime / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    db.init()
    return config.DB_PATH


def test_contour_heartbeat_is_user_scoped_and_turns_stale_closed(
    control_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(contours.time, "time", lambda: 1000)
    contours.heartbeat(
        100,
        {
            "id": "vscode-mac",
            "label": "MacBook Ильи",
            "kind": "local",
            "state": "working",
            "taskCount": 2,
            "title": "этот текст не должен храниться",
        },
    )

    assert contours.list_for_user(200) == []
    current = contours.list_for_user(100)
    assert current[0]["state"] == "working"
    assert current[0]["taskCount"] == 2
    assert "title" not in current[0]

    monkeypatch.setattr(contours.time, "time", lambda: 1100)
    stale = contours.list_for_user(100, live_after_sec=45)
    assert stale[0]["state"] == "closed"
    assert stale[0]["taskCount"] == 0


def test_contour_payload_validation_fails_closed(control_database: Path) -> None:
    with pytest.raises(contours.ContourError):
        contours.heartbeat(
            100,
            {"id": "../escape", "label": "Mac", "kind": "local", "state": "working"},
        )


def test_control_request_lifecycle_is_explicit(control_database: Path) -> None:
    request_id = control.request_stop(100)
    duplicate_id = control.request_stop(100)

    rows = control.pending()
    assert duplicate_id == request_id
    assert rows == [
        {"id": request_id, "user_id": 100, "action": "stop", "created_at": rows[0]["created_at"]}
    ]

    control.mark_handled(request_id, cancelled=2)
    assert control.pending() == []


def test_integration_state_is_atomic_bounded_and_local(
    control_database: Path, tmp_path: Path
) -> None:
    result = integration_state.write(
        "vscode-window-1",
        state="working",
        cwd=str(tmp_path),
        task_count=4,
        title="  Большая\n задача  ",
        session_id="session-1",
    )

    path = integration_state.state_path("vscode-window-1")
    assert path.exists()
    assert result["title"] == "Большая задача"
    assert result["taskCount"] == 4
    assert not list(path.parent.glob("*.tmp"))
