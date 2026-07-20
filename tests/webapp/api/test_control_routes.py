from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from core import config, contours, control, db
from webapp.api import server


def configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "ADMIN_IDS", [100])
    monkeypatch.setattr(config, "ADMIN_ID", 100)
    monkeypatch.setattr(server, "DEV_SKIP_AUTH", True)
    db.init()


async def test_stop_endpoint_is_user_scoped_and_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(tmp_path, monkeypatch)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        first = await client.post("/api/control/stop", json={})
        second = await client.post("/api/control/stop", json={})

        assert first.status == 202
        assert second.status == 202
        assert (await first.json())["requestId"] == (await second.json())["requestId"]
        assert len(control.pending()) == 1
        assert control.pending()[0]["user_id"] == 100
    finally:
        await client.close()


async def test_contour_heartbeat_and_close_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(tmp_path, monkeypatch)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        heartbeat = await client.post(
            "/api/contours/heartbeat",
            json={
                "id": "vscode-test",
                "label": "Test contour",
                "kind": "local",
                "state": "working",
                "taskCount": 3,
                "title": "must not be stored",
            },
        )
        assert heartbeat.status == 200
        assert (await heartbeat.json())["taskCount"] == 3
        assert "title" not in contours.list_for_user(100)[0]

        closed = await client.post("/api/contours/close", json={"id": "vscode-test"})
        assert closed.status == 200
        assert await closed.json() == {"closed": True}
        assert contours.list_for_user(100)[0]["state"] == "closed"
    finally:
        await client.close()


async def test_contour_endpoint_rejects_unsafe_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(tmp_path, monkeypatch)
    client = TestClient(TestServer(server.create_app()))
    await client.start_server()
    try:
        response = await client.post(
            "/api/contours/heartbeat",
            json={
                "id": "../escape",
                "label": "Bad contour",
                "kind": "local",
                "state": "working",
            },
        )
        assert response.status == 400
        assert await response.json() == {"error": "invalid_identity"}
    finally:
        await client.close()
