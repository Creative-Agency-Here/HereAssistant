"""Арбитраж локального и удалённого ввода в координаторе /rc.

Гонки проверяются управляющими событиями asyncio, а НЕ sleep: запуск блокируется
на ``asyncio.Event``, а порядок и отсутствие второго запуска утверждаются после
детерминированного ожидания нужного состояния.
"""

from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from typing import cast

import pytest

import chat
from chat_commands import CommandRouter
from chat_remote_control import QueuedItem, RemoteControlCoordinator
from chat_sessions import AccountRecord, Session
from core import config, db, project_config
from core.remote_control import publications
from core.remote_control.control_plane_client import PublicationClosedError

# Приватный проект с явным правом на presence (для публикации в тестах).
PRESENCE_POLICY = project_config.ProjectPolicy(
    mode="private", rc_enabled=True, rc_allow_presence_in_private=True
)


@pytest.fixture
def rc_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Изолированная SQLite: все пути рантайма уводим во временный каталог.
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


def account() -> AccountRecord:
    return cast(
        AccountRecord,
        {
            "label": "main",
            "provider": "claude_code",
            "default_model": "model-a",
            "enabled": True,
            "cli_home_path": "/tmp/home",
        },
    )


def make_session(monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr(config, "user_default_cwd", lambda _user_id: "/workspace/1")
    return Session(account(), 1, "@alice")


class RunTracker:
    """Управляемый заменитель провайдера: блокируется на событии, считает запуски."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.block: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def run(self, prompt: str) -> tuple[bool, str]:
        self.calls.append(prompt)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.started.set()
        try:
            if self.block is not None:
                await self.block.wait()
            return True, f"ответ на {prompt}"
        finally:
            self.concurrent -= 1
            self.started.clear()


def make_coordinator(
    session: Session, tracker: RunTracker
) -> RemoteControlCoordinator:
    return RemoteControlCoordinator(
        session,
        run_prompt=tracker.run,
        output=StringIO(),
        policy_lookup=lambda _cwd: PRESENCE_POLICY,
        device_id="dev-test",
    )


# ---------- 1. локальный ввод во время удалённого запуска ----------
async def test_local_input_queues_behind_remote_run(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    tracker = RunTracker()
    tracker.block = asyncio.Event()
    coordinator = make_coordinator(session, tracker)

    remote = coordinator.submit_remote(
        "удалённый", command_id="c1", sequence=1, payload_hash="h1"
    )
    assert remote.started_now
    await tracker.started.wait()  # удалённый запуск точно работает

    local = coordinator.submit_local("локальный")
    assert not local.started_now
    assert local.position == 1
    # второго запуска нет: пока занят замок, провайдер один
    assert tracker.concurrent == 1
    assert tracker.max_concurrent == 1

    tracker.block.set()
    await local.item.done
    assert tracker.calls == ["удалённый", "локальный"]
    assert tracker.max_concurrent == 1


# ---------- 2. удалённый ввод во время локального запуска ----------
async def test_remote_input_queues_behind_local_run(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    tracker = RunTracker()
    tracker.block = asyncio.Event()
    coordinator = make_coordinator(session, tracker)

    local = coordinator.submit_local("локальный")
    assert local.started_now
    await tracker.started.wait()  # локальный запуск точно работает

    remote = coordinator.submit_remote(
        "удалённый", command_id="c1", sequence=1, payload_hash="h1"
    )
    assert remote is not None and not remote.started_now
    assert tracker.concurrent == 1
    assert tracker.max_concurrent == 1

    tracker.block.set()
    await remote.item.done
    assert tracker.calls == ["локальный", "удалённый"]
    assert tracker.max_concurrent == 1


# ---------- 3. /rc off очищает очередь и снимает публикацию ----------
async def test_rc_off_clears_queue_and_closes_publication(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    tracker = RunTracker()
    tracker.block = asyncio.Event()
    coordinator = make_coordinator(session, tracker)

    assert coordinator.publish()
    assert coordinator.is_active()

    first = coordinator.submit_local("первый")
    await tracker.started.wait()
    second = coordinator.submit_local("второй")
    assert second.position == 1

    coordinator.off()
    assert not coordinator.is_active()
    assert coordinator.queue_snapshot() == []
    assert session.rc_publication is None
    publication = publications.get(coordinator._key())  # noqa: SLF001
    assert publication is not None and publication["state"] == "closed"

    # текущий запуск завершается, но очищенный из очереди уже не стартует
    tracker.block.set()
    await first.item.done
    await asyncio.sleep(0)  # даём циклу проверить пустую очередь
    assert tracker.calls == ["первый"]


# ---------- 4. выход из чата снимает публикацию даже при исключении ----------
async def test_repl_exit_removes_publication_even_on_error(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Проект с разрешённым presence, чтобы /rc опубликовал сессию.
    project = tmp_path / "proj"
    (project / ".hereassistant").mkdir(parents=True)
    (project / ".hereassistant" / "project.yml").write_text(
        "mode: private\n"
        "remote_control:\n"
        "  enabled: true\n"
        "  allow_presence_in_private: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chat.config, "user_default_cwd", lambda _user_id: str(project))
    monkeypatch.setattr(chat, "task_summary", lambda _cwd: {"open": 0})
    monkeypatch.setattr(
        chat,
        "workspace_overview",
        lambda _user_id, _cwd: {
            "tasks": {"open": 0},
            "git": {"repositories": 0},
            "repositoriesOnDisk": 0,
            "disk": {"freeLabel": "1 ГБ"},
        },
    )
    monkeypatch.setattr(chat, "_farewell", lambda: None)

    class FakeTitle:
        def idle(self, cwd: str, open_tasks: int = 0) -> None:
            pass

        def start(self, prompt: str, task_count: int) -> None:
            pass

        async def finish(self, *, completed: bool, cwd: str, open_tasks: int = 0) -> None:
            pass

    monkeypatch.setattr(chat, "TerminalTitle", FakeTitle)

    class Boom(Exception):
        pass

    lines = iter(["/rc"])

    class FakePrompt:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def read(self, prompt: str) -> str:
            try:
                return next(lines)
            except StopIteration:
                raise Boom() from None

    monkeypatch.setattr(chat, "TerminalPrompt", FakePrompt)

    session = Session(account(), 1, "@alice")
    with pytest.raises(Boom):
        await chat._repl(session)  # noqa: SLF001

    # Публикация создана командой /rc и гарантированно снята в finally.
    with db.conn() as connection:
        rows = list(connection.execute("SELECT state FROM rc_publications"))
    assert rows, "публикация должна была создаться командой /rc"
    assert all(row["state"] == "closed" for row in rows)
    assert session.rc_publication is None


# ---------- 5. смена рабочей папки при активной публикации отклоняется ----------
async def test_cwd_change_rejected_while_published(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    original_cwd = session.cwd
    tracker = RunTracker()
    coordinator = make_coordinator(session, tracker)
    assert coordinator.publish()

    output = StringIO()
    router = CommandRouter(
        accounts=lambda _user_id: [account()],
        users=lambda: [],
        default_cwd=lambda user_id: f"/workspace/{user_id}",
        resumable=lambda _session: [],
        output=output,
        rc=coordinator,
    )

    target = tmp_path / "other"
    target.mkdir()
    router.handle(session, f"/cwd {target}")

    assert session.cwd == original_cwd
    assert "публикация /rc" in output.getvalue()

    # после снятия публикации смена разрешена
    coordinator.off()
    router.handle(session, f"/cwd {target}")
    assert session.cwd == str(target.resolve())


# ---------- дополнительные инварианты ----------
async def test_fifo_local_priority_on_equal_accept_moment(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    coordinator = make_coordinator(session, RunTracker())
    # Равный момент принятия: первым должен исполниться локальный ввод.
    coordinator._queue.extend(  # noqa: SLF001
        [
            QueuedItem(source="remote", prompt="r", accepted_seq=5, command_id="c"),
            QueuedItem(source="local", prompt="l", accepted_seq=5),
        ]
    )
    head = coordinator._pop_next()  # noqa: SLF001
    assert head is not None and head.source == "local"


async def test_remote_duplicate_command_is_not_executed_twice(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    tracker = RunTracker()
    coordinator = make_coordinator(session, tracker)

    first = coordinator.submit_remote(
        "prompt", command_id="dup", sequence=1, payload_hash="h"
    )
    assert first is not None
    second = coordinator.submit_remote(
        "prompt", command_id="dup", sequence=1, payload_hash="h"
    )
    assert second is None  # дубль отклонён receipt-ом
    await first.item.done
    assert tracker.calls == ["prompt"]


async def test_remote_input_cannot_change_session_settings(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    session.permission_mode = "account"
    tracker = RunTracker()
    coordinator = make_coordinator(session, tracker)

    before = (session.label, session.model, session.cwd, session.permission_mode)
    result = coordinator.submit_remote(
        "что-нибудь", command_id="c1", sequence=1, payload_hash="h1"
    )
    assert result is not None
    await result.item.done
    after = (session.label, session.model, session.cwd, session.permission_mode)
    assert before == after


# ---------- снятие публикации владельцем из интерфейса ----------
async def test_publication_closed_by_owner_stops_local_publishing(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сервер отказал heartbeat — координатор снимает публикацию у себя.

    Регрессия: сервер молча принимал heartbeat закрытой публикации и возвращал
    её в живое состояние, поэтому кнопка «Завершить удалённое управление» не
    работала, пока в терминале включён /rc. Теперь сервер отвечает 409, а
    устройство обязано согласиться, а не долбиться в закрытую публикацию.
    """
    session = make_session(monkeypatch)
    tracker = RunTracker()
    coordinator = make_coordinator(session, tracker)

    class ClosedByOwnerClient:
        """Минимальный контроль-плейн: публикация уже снята владельцем."""

        def __init__(self) -> None:
            self.heartbeats = 0

        def configured(self) -> bool:
            return True

        async def create_publication(self, **_: object) -> str:
            return "pub-remote-1"

        async def list_commands(self, **_: object) -> list[dict[str, object]]:
            return []

        async def heartbeat(self, **_: object) -> bool:
            self.heartbeats += 1
            raise PublicationClosedError()

    client = ClosedByOwnerClient()
    coordinator._client = client  # noqa: SLF001 — точка внедрения в тестах
    coordinator.publish()
    assert coordinator._active is True  # noqa: SLF001

    # Ждём, пока сетевой цикл упрётся в отказ и снимет публикацию у себя.
    for _ in range(200):
        if not coordinator._active:  # noqa: SLF001
            break
        await asyncio.sleep(0.01)

    assert coordinator._active is False, 'публикация должна быть снята локально'  # noqa: SLF001
    assert session.rc_publication is None
    # Долбиться в закрытую публикацию нельзя: heartbeat не повторяется.
    before = client.heartbeats
    await asyncio.sleep(0.05)
    assert client.heartbeats == before
