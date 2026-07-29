"""Сборка проводки /rc: клиент control-plane, все типы команд, слив outbox.

Проверяет то, что раньше было реализовано частями, но не соединено:
* без URL И credential ``ControlPlaneClient`` не создаётся вовсе — ноль
  сетевых обращений в принципе (объекта, которым можно позвонить, нет);
* удалённый ``git_commit`` доходит до ``core/remote_control/git_actions.py``
  и уважает его privacy-гейт (``can_execute_rc_git``);
* ``stop`` останавливает текущий запуск;
* ``approval_decision`` без живого канала подтверждения — явный отказ,
  никогда не автоодобрение;
* событие уходит из outbox ровно один раз и помечается доставленным;
* неудачная доставка растит счётчик попыток и не теряет событие.

Сеть нигде не используется: aiohttp-сессия/control-plane клиент — заглушки.
"""

from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest

import chat_remote_control
from chat_remote_control import RemoteControlCoordinator, resolve_control_client
from chat_sessions import AccountRecord, Session
from core import config, db, git_projects
from core.project_config import PRIVATE, ProjectPolicy
from core.remote_control import config as rc_config
from core.remote_control import outbox, publications, receipts
from core.remote_control.credential_store import DeviceCredential

FULL_SHA = "b" * 40
REMOTE_URL = "https://github.com/example/project.git"


@pytest.fixture
def rc_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Изолированная SQLite: все пути рантайма уводим во временный каталог
    # (тот же паттерн, что в tests/test_chat_remote_control.py).
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


def make_session(monkeypatch: pytest.MonkeyPatch, cwd: str = "/workspace/1") -> Session:
    monkeypatch.setattr(config, "user_default_cwd", lambda _user_id: cwd)
    return Session(account(), 1, "@alice")


class RunTracker:
    """Управляемый заменитель провайдера: блокируется на событии."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.block: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def run(self, prompt: str) -> tuple[bool, str]:
        self.calls.append(prompt)
        self.started.set()
        try:
            if self.block is not None:
                await self.block.wait()
            return True, f"ответ на {prompt}"
        finally:
            self.started.clear()


def make_coordinator(
    session: Session, tracker: RunTracker, *, policy: ProjectPolicy
) -> RemoteControlCoordinator:
    return RemoteControlCoordinator(
        session,
        run_prompt=tracker.run,
        output=StringIO(),
        policy_lookup=lambda _cwd: policy,
        device_id="dev-test",
    )


def credential() -> DeviceCredential:
    return DeviceCredential(token="harc_TEST_TOKEN", device_id="dev-remote-1")


# ---------- 1. без URL И credential — ноль сетевых обращений ----------


def test_no_network_call_without_url_or_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    def _must_not_be_called(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError(
            "ControlPlaneClient не должен создаваться без URL и credential устройства"
        )

    monkeypatch.setattr(chat_remote_control, "ControlPlaneClient", _must_not_be_called)

    # Ни URL, ни credential.
    monkeypatch.setattr(rc_config, "configured", lambda: False)
    client, device_id = resolve_control_client(credential_loader=lambda: credential())
    assert client is None
    assert device_id is None

    # URL задан, credential — нет: режим всё ещё выключен целиком.
    monkeypatch.setattr(rc_config, "configured", lambda: True)
    client2, device_id2 = resolve_control_client(credential_loader=lambda: None)
    assert client2 is None
    assert device_id2 is None


def test_control_client_created_only_when_both_url_and_credential_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rc_config, "configured", lambda: True)
    cred = credential()
    client, device_id = resolve_control_client(credential_loader=lambda: cred)
    assert client is not None
    assert client.configured() is False  # base_url по умолчанию пуст в этом окружении теста
    assert device_id == cred.device_id


# ---------- 2. git_commit доходит до git_actions и уважает privacy-гейт ----------


class GitStub:
    """Заглушка core.git_projects.run_git — повторяет паттерн test_remote_control_git.py."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.responses: dict[tuple, object] = {}

    async def __call__(self, *args: str, **_kwargs: object) -> str:
        self.calls.append(args)
        result = self.responses.get(args, "")
        if isinstance(result, BaseException):
            raise result
        return result


async def _allow_grant(*_args: object, **_kwargs: object) -> None:
    return None


def crm_git_policy() -> ProjectPolicy:
    return ProjectPolicy(mode="crm", crm_project_id="p1", sync_enabled=True, rc_enabled=True)


async def test_remote_git_commit_denied_by_privacy_gate_never_touches_git(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = GitStub()
    monkeypatch.setattr(git_projects, "run_git", stub)
    monkeypatch.setattr(git_projects, "require_repository_grant", _allow_grant)

    project_dir = tmp_path / "proj"
    (project_dir / ".hereassistant").mkdir(parents=True)
    (project_dir / ".hereassistant" / "project.yml").write_text("mode: private\n", encoding="utf-8")

    session = make_session(monkeypatch, cwd=str(project_dir))
    tracker = RunTracker()
    # PRIVATE (в т.ч. параметр из fixture) не проходит can_execute_rc_git.
    coordinator = make_coordinator(session, tracker, policy=PRIVATE)

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "gc-denied",
            "sequence": 1,
            "commandType": "git_commit",
            "payload": {"paths": ["a.py"], "message": "m"},
        }
    )
    # Git-действие уходит в отдельную asyncio-задачу — дождаться именно её.
    new_tasks = asyncio.all_tasks() - before
    assert len(new_tasks) == 1
    await next(iter(new_tasks))

    assert stub.calls == []  # гейт сработал ДО обращения к Git
    receipt = receipts.get("gc-denied")
    assert receipt is not None
    assert receipt["state"] == "failed"


async def test_remote_git_commit_reaches_git_actions_when_allowed(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = GitStub()
    stub.responses.update(
        {
            ("remote", "get-url", "origin"): REMOTE_URL,
            ("rev-parse", "HEAD"): FULL_SHA,
        }
    )
    monkeypatch.setattr(git_projects, "run_git", stub)
    monkeypatch.setattr(git_projects, "require_repository_grant", _allow_grant)

    project_dir = tmp_path / "proj"
    (project_dir / ".hereassistant").mkdir(parents=True)
    (project_dir / ".hereassistant" / "project.yml").write_text("mode: crm\n", encoding="utf-8")

    session = make_session(monkeypatch, cwd=str(project_dir))
    tracker = RunTracker()
    coordinator = make_coordinator(session, tracker, policy=crm_git_policy())

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "gc-ok",
            "sequence": 1,
            "commandType": "git_commit",
            "payload": {"paths": ["a.py", "dir/b.py"], "message": "fix: правки"},
        }
    )
    new_tasks = asyncio.all_tasks() - before
    assert len(new_tasks) == 1
    await next(iter(new_tasks))

    assert ("add", "--", "a.py", "dir/b.py") in stub.calls
    assert ("commit", "-m", "fix: правки") in stub.calls
    receipt = receipts.get("gc-ok")
    assert receipt is not None
    assert receipt["state"] == "succeeded"


# ---------- 3. stop останавливает текущий запуск ----------


async def test_remote_stop_cancels_running_prompt(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    tracker = RunTracker()
    tracker.block = asyncio.Event()
    coordinator = make_coordinator(session, tracker, policy=crm_git_policy())

    submitted = coordinator.submit_remote(
        "долгий промпт", command_id="p1", sequence=1, payload_hash="h1"
    )
    assert submitted is not None
    await tracker.started.wait()

    coordinator._ingest_remote_command(  # noqa: SLF001
        {"id": "stop-1", "sequence": 2, "commandType": "stop", "payload": {}}
    )

    completed, _answer = await submitted.item.done
    assert completed is False  # запуск отменён, а не завершён успешно

    receipt = receipts.get("stop-1")
    assert receipt is not None
    assert receipt["state"] == "succeeded"  # сама команда stop исполнена


# ---------- 4. approval_decision без живого канала — отказ, не автоодобрение ----------


async def test_remote_approval_decision_fails_closed(
    rc_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(monkeypatch)
    tracker = RunTracker()
    coordinator = make_coordinator(session, tracker, policy=crm_git_policy())

    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "appr-1",
            "sequence": 1,
            "commandType": "approval_decision",
            "payload": {"decision": "approve"},
        }
    )

    receipt = receipts.get("appr-1")
    assert receipt is not None
    # Отказ, а НЕ succeeded/автоодобрение: живого approval-канала у провайдера нет.
    assert receipt["state"] == "rejected"


# ---------- 5 и 6. слив outbox: доставка ровно один раз / retry без потери ----------


class _FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeSession:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"ok": True}
        self.posts: list[tuple[str, Any]] = []

    def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        self.posts.append((url, json))
        return _FakeResponse(self.status, self.payload)


def _make_client(session: _FakeSession):
    from core.remote_control.control_plane_client import ControlPlaneClient

    return ControlPlaneClient(base_url="https://cp.example.com", session=session)


def _make_publication(remote_public_id: str) -> int:
    """Локальная публикация с уже известным серверным UUID — как после
    подтверждённого control-plane ``POST publications`` (вне зоны этого теста).
    """
    row = publications.publish(
        f"outbox-test:{remote_public_id}",
        policy=crm_git_policy(),
        device_id="dev-outbox",
        remote_public_id=remote_public_id,
    )
    assert row is not None
    return int(row["id"])


async def test_outbox_event_delivered_exactly_once(rc_database: Path) -> None:
    publication_uuid = "11111111-1111-4111-8111-111111111111"
    publication_id = _make_publication(publication_uuid)
    event_id = "00000000-0000-4000-8000-0000000000ee"
    outbox.enqueue(
        {"type": "rc.command_status", "commandId": "cmd-1", "state": "succeeded"},
        event_id=event_id,
        publication_id=publication_id,
        now=1000,
    )

    session = _FakeSession(status=200, payload={"acceptedCount": 1, "duplicateEventIds": []})
    client = _make_client(session)

    delivered = await outbox.flush_once(client)
    assert delivered is True
    assert len(session.posts) == 1
    url, body = session.posts[0]
    assert url == f"https://cp.example.com/cli-agent/runner/publications/{publication_uuid}/events"
    assert body == {
        "events": [
            {
                "eventId": event_id,
                "type": "rc.command_status",
                "commandId": "cmd-1",
                "payload": {"state": "succeeded"},
            }
        ]
    }
    assert outbox.pending_count() == 0

    # Второй прогон: слать больше нечего — повторной доставки нет.
    delivered_again = await outbox.flush_once(client)
    assert delivered_again is False
    assert len(session.posts) == 1  # ни одного лишнего HTTP-вызова


async def test_outbox_failed_delivery_retries_without_losing_event(
    rc_database: Path,
) -> None:
    publication_id = _make_publication("22222222-2222-4222-8222-222222222222")
    event_id = "00000000-0000-4000-8000-0000000000ff"
    outbox.enqueue(
        {"type": "rc.command_status", "state": "failed"},
        event_id=event_id,
        publication_id=publication_id,
        now=1000,
    )

    session = _FakeSession(status=500, payload=None)
    client = _make_client(session)

    delivered = await outbox.flush_once(client)
    assert delivered is False
    # Событие не потеряно — оно всё ещё в очереди, просто попытка отодвинута.
    assert outbox.pending_count() == 1
    due = outbox.next_due(now=1000)
    assert due is None  # немедленно повторно не готово (экспоненциальный delay)

    with db.conn() as connection:
        row = connection.execute(
            "SELECT attempts FROM rc_event_outbox WHERE event_id=?", (event_id,)
        ).fetchone()
    assert row["attempts"] == 1


async def test_outbox_event_deferred_until_publication_confirmed(rc_database: Path) -> None:
    """Событие без известного серверного UUID публикации не отправляется вслепую.

    Это НЕ сбой доставки конкретного события — просто локальная публикация ещё
    не подтверждена control-plane (``POST publications`` вне зоны текущей
    доработки). Событие остаётся в очереди и не улетает на несуществующий адрес.
    """
    event_id = "00000000-0000-4000-8000-0000000000aa"
    outbox.enqueue({"type": "rc.command_status", "state": "running"}, event_id=event_id, now=1000)

    session = _FakeSession(status=200, payload={"acceptedCount": 1, "duplicateEventIds": []})
    client = _make_client(session)

    delivered = await outbox.flush_once(client)
    assert delivered is False
    assert session.posts == []  # ни одного HTTP-вызова с невалидным publicationId
    assert outbox.pending_count() == 1


# ---------- 8. проводка публикации к серверу ----------


class PublishingClient:
    """Заглушка control-plane: помнит, что именно вызвал раннер."""

    def __init__(self, publication_id: str = "srv-pub-1") -> None:
        self.publication_id = publication_id
        self.created: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.closed: list[str] = []

    def configured(self) -> bool:
        return True

    async def create_publication(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return self.publication_id

    async def submit_command_result(self, **kwargs: Any) -> bool:
        self.results.append(kwargs)
        return True

    async def close_publication(self, *, publication_id: str) -> bool:
        self.closed.append(publication_id)
        return True

    async def list_commands(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def heartbeat(self, **_kwargs: Any) -> bool:
        return True


async def test_publication_registered_on_server_and_id_stored(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без серверного идентификатора адресовать команды и heartbeat нечем."""
    session = make_session(monkeypatch, cwd=str(tmp_path))
    coordinator = make_coordinator(session, RunTracker(), policy=crm_git_policy())
    client = PublishingClient()
    coordinator._client = client  # noqa: SLF001

    assert coordinator.publish() is True
    await coordinator._ensure_remote_publication(client)  # noqa: SLF001

    assert len(client.created) == 1
    stored = publications.get(coordinator._key())  # noqa: SLF001
    assert stored is not None
    assert stored["remote_public_id"] == "srv-pub-1"

    # Повторный проход не создаёт вторую публикацию.
    await coordinator._ensure_remote_publication(client)  # noqa: SLF001
    assert len(client.created) == 1


async def test_command_result_reported_to_server(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Терминальный статус сервер узнаёт из результата команды, не из событий."""
    session = make_session(monkeypatch, cwd=str(tmp_path))
    coordinator = make_coordinator(session, RunTracker(), policy=crm_git_policy())
    client = PublishingClient()
    coordinator._client = client  # noqa: SLF001
    coordinator.publish()
    await coordinator._ensure_remote_publication(client)  # noqa: SLF001

    await coordinator._report_command_result("cmd-1", "succeeded")  # noqa: SLF001

    assert len(client.results) == 1
    assert client.results[0]["publication_id"] == "srv-pub-1"
    assert client.results[0]["command_id"] == "cmd-1"
    assert client.results[0]["status"] == "succeeded"


async def test_rc_off_closes_publication_on_server(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/rc off` обязан снять публикацию и на сервере, а не только локально."""
    session = make_session(monkeypatch, cwd=str(tmp_path))
    coordinator = make_coordinator(session, RunTracker(), policy=crm_git_policy())
    client = PublishingClient()
    coordinator._client = client  # noqa: SLF001
    coordinator.publish()
    await coordinator._ensure_remote_publication(client)  # noqa: SLF001

    before = asyncio.all_tasks()
    coordinator.off()
    for task in asyncio.all_tasks() - before:
        await task

    assert client.closed == ["srv-pub-1"]
