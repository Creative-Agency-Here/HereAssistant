from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import workspace_status
from webapp.api.routes import connections


def test_git_snapshot_reports_push_and_dirty_without_file_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = iter(
        (
            "# branch.head master\n# branch.ab +2 -0\n1 .M N... 100644 100644 100644 a b secret.txt",
            "abcdef1234567890",
        )
    )
    monkeypatch.setattr(workspace_status, "_run_git", lambda _root, *_args: next(outputs))

    result = workspace_status.git_snapshot(tmp_path)

    assert result == {
        "available": True,
        "branch": "master",
        "head": "abcdef123456",
        "dirty": 1,
        "ahead": 2,
        "behind": 0,
        "state": "changes",
    }
    assert "secret.txt" not in str(result)


def test_deployment_snapshot_requires_exact_hook_marker(tmp_path: Path) -> None:
    marker = tmp_path / ".hereassistant" / "deploy-state.json"
    marker.parent.mkdir()
    marker.write_text(
        json.dumps(
            {
                "targets": {
                    "admin": {"commit": "abcdef123456", "status": "deployed"},
                    "site": {"commit": "000000000000", "status": "pending"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = workspace_status.deployment_snapshot(tmp_path, "abcdef123456")

    assert result["state"] == "partial"
    assert [target["name"] for target in result["targets"]] == ["admin", "site"]


def test_contours_merge_local_and_remote_sessions() -> None:
    result = connections._contours(  # noqa: SLF001
        {"id": "de-1", "label": "Сервер Германия", "kind": "server", "originHost": "de-1"},
        [
            {
                "originHost": "macbook",
                "lastActivityAt": "2020-01-01T00:00:00+00:00",
            },
            {
                "originHost": "macbook",
                "lastActivityAt": "2020-01-02T00:00:00+00:00",
            },
        ],
        local_working=True,
    )

    assert result[0]["label"] == "Сервер Германия"
    assert result[0]["state"] == "working"
    remote = next(item for item in result if item["originHost"] == "macbook")
    assert remote["sessions"] == 2
    assert remote["state"] == "closed"


def test_live_heartbeat_overrides_estimated_crm_contour() -> None:
    result = connections._merge_heartbeats(  # noqa: SLF001
        [
            {
                "id": "macbook",
                "label": "macbook",
                "kind": "remote",
                "originHost": "macbook",
                "local": False,
                "state": "closed",
                "estimated": True,
            }
        ],
        [
            {
                "id": "macbook",
                "label": "MacBook Ильи",
                "kind": "local",
                "originHost": "macbook",
                "local": False,
                "state": "working",
                "estimated": False,
                "taskCount": 1,
            }
        ],
    )

    assert result[0]["label"] == "MacBook Ильи"
    assert result[0]["state"] == "working"
    assert result[0]["estimated"] is False
