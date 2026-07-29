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
import hashlib
import json
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest

import chat_remote_control
from chat_remote_control import RemoteControlCoordinator, resolve_control_client
from chat_sessions import AccountRecord, Session
from core import config, crm_sync, db, git_projects
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
        self.methods: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        json: Any = None,
        params: Any = None,
        headers: Any = None,
    ) -> _FakeResponse:
        """Клиент зовёт сессию объявленным методом — заглушка это фиксирует."""
        self.methods.append((method.upper(), url))
        self.posts.append((url, json if json is not None else params))
        return _FakeResponse(self.status, self.payload)

    def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        return self.request("POST", url, json=json, headers=headers)


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


async def _drain_tasks(before: set) -> None:
    """Дожидается всех задач, порождённых координатором (в т.ч. вложенных).

    Отчёт серверу и запуск промпта уезжают в отдельные задачи; sleep для их
    ожидания не годится — ждём именно задачи, пока их не останется.
    """
    for _ in range(20):
        pending = [
            task
            for task in asyncio.all_tasks() - before
            if task is not asyncio.current_task() and not task.done()
        ]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def presence_only_policy() -> ProjectPolicy:
    """Приватный проект с presence: публикация есть, удалённые промпты — нет."""
    return ProjectPolicy(
        mode="private", rc_enabled=True, rc_allow_presence_in_private=True
    )


def crm_prompt_policy(**flags: bool) -> ProjectPolicy:
    """CRM-политика, разрешающая удалённый промпт (и явные флаги стриминга)."""
    sync_flags = {"send_prompts": True}
    sync_flags.update({f"send_{name}": value for name, value in flags.items()})
    return ProjectPolicy(
        mode="crm",
        crm_project_id="p1",
        sync_enabled=True,
        rc_enabled=True,
        sync_flags=sync_flags,
    )


async def _published_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracker: RunTracker,
    *,
    policy: ProjectPolicy,
) -> tuple[RemoteControlCoordinator, PublishingClient]:
    """Координатор с опубликованной сессией и известным серверным UUID."""
    session = make_session(monkeypatch, cwd=str(tmp_path))
    coordinator = make_coordinator(session, tracker, policy=policy)
    client = PublishingClient()
    coordinator._client = client  # noqa: SLF001
    assert coordinator.publish() is True
    await coordinator._ensure_remote_publication(client)  # noqa: SLF001
    return coordinator, client


# ---------- 9. отказ по приватности доезжает до сервера с собственным кодом ----------


async def test_privacy_denied_prompt_reports_failed_once_with_code(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Локального receipt недостаточно: без отчёта команда висит claimed вечно.

    ``rejected`` серверу отправить нельзя (его нет в контракте), поэтому наружу
    уезжает ``failed`` плюс код причины — ровно один раз.
    """
    tracker = RunTracker()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=presence_only_policy()
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "p-private",
            "sequence": 1,
            "commandType": "prompt",
            "payload": {"prompt": "сделай что-нибудь"},
        }
    )
    await _drain_tasks(before)

    assert tracker.calls == []  # провайдер не запускался
    assert receipts.get("p-private")["state"] == "rejected"  # локально — честный отказ
    assert len(client.results) == 1
    assert client.results[0]["status"] == "failed"
    assert client.results[0]["error_code"] == chat_remote_control.REASON_PRIVACY_DENIED


async def test_approval_decision_has_its_own_reason_code(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Человек в Telegram должен видеть разницу между запретом проекта и тем, что
    # подтверждение инструмента можно дать только за компьютером.
    tracker = RunTracker()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy()
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "appr-code",
            "sequence": 1,
            "commandType": "approval_decision",
            "payload": {"decision": "approve"},
        }
    )
    await _drain_tasks(before)

    assert len(client.results) == 1
    assert client.results[0]["status"] == "failed"
    assert (
        client.results[0]["error_code"]
        == chat_remote_control.REASON_APPROVAL_LOCAL_ONLY
    )


async def test_unknown_command_type_is_refused_with_code(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = RunTracker()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy()
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {"id": "weird-1", "sequence": 1, "commandType": "shell", "payload": {}}
    )
    await _drain_tasks(before)

    assert tracker.calls == []
    assert receipts.get("weird-1") is None  # receipt неизвестному типу не создаётся
    assert client.results[0]["error_code"] == (
        chat_remote_control.REASON_UNKNOWN_COMMAND_TYPE
    )


# ---------- 10. пустой промпт не запускает провайдера ----------


async def test_empty_prompt_never_starts_provider(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = RunTracker()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy()
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "p-empty",
            "sequence": 1,
            "commandType": "prompt",
            "payload": {"prompt": "   \n"},
        }
    )
    await _drain_tasks(before)

    assert tracker.calls == []  # запуск без текста слот исполнения не занимает
    assert len(client.results) == 1
    assert client.results[0]["status"] == "failed"
    assert client.results[0]["error_code"] == chat_remote_control.REASON_EMPTY_PROMPT


# ---------- 11. хеш payload: регистр поля и канон расчёта ----------


def test_local_payload_hash_matches_server_canon() -> None:
    """Локальный расчёт повторяет ``remote-control.shared.ts`` посимвольно.

    Сервер считает ``sha256(JSON.stringify(canonicalize(payload ?? {})))``:
    рекурсивно отсортированные ключи и компактные разделители. Разделители по
    умолчанию (``", "``) давали заведомо другой хеш.
    """
    payload = {"b": 1, "a": {"y": 2, "x": [3, {"n": 4}]}}
    canonical = '{"a":{"x":[3,{"n":4}],"y":2},"b":1}'
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert chat_remote_control._hash_payload(payload) == expected  # noqa: SLF001
    # payload ?? {} на сервере: пустой и отсутствующий payload дают один хеш.
    empty = hashlib.sha256(b"{}").hexdigest()
    assert chat_remote_control._hash_payload(None) == empty  # noqa: SLF001
    assert chat_remote_control._hash_payload({}) == empty  # noqa: SLF001


async def test_payload_hash_is_read_from_camel_case_field(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drizzle отдаёт колонки camelCase — читать надо ``payloadHash``.

    Чтение ``payload_hash`` всегда давало None, и сверка на подмену payload
    фактически сравнивала локальный хеш с локальным.
    """
    tracker = RunTracker()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy()
    )
    server_hash = "a" * 64

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "p-hash",
            "sequence": 1,
            "commandType": "prompt",
            "payload": {"prompt": "привет"},
            "payloadHash": server_hash,
        }
    )
    await _drain_tasks(before)

    receipt = receipts.get("p-hash")
    assert receipt is not None
    assert receipt["payload_hash"] == server_hash
    assert tracker.calls == ["привет"]


async def test_payload_mismatch_is_refused_with_code(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Подмена payload при повторной доставке — fail closed, и сервер обязан
    # узнать причину кодом, а не ждать таймаута отправителя.
    tracker = RunTracker()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy()
    )

    before = asyncio.all_tasks()
    for payload_hash in ("b" * 64, "c" * 64):
        coordinator._ingest_remote_command(  # noqa: SLF001
            {
                "id": "p-mismatch",
                "sequence": 1,
                "commandType": "prompt",
                "payload": {"prompt": "первый"},
                "payloadHash": payload_hash,
            }
        )
    await _drain_tasks(before)

    assert tracker.calls == ["первый"]  # второй раз ничего не запускалось
    codes = [item.get("error_code") for item in client.results]
    assert chat_remote_control.REASON_PAYLOAD_MISMATCH in codes


# ---------- 12. сбой запуска и терминальное событие turn-а ----------


class FailingRunner:
    """Провайдер, честно падающий — сбой обязан назвать свою причину."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.block: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def run(self, prompt: str) -> tuple[bool, str]:
        self.calls.append(prompt)
        raise RuntimeError("провайдер недоступен")


async def test_provider_failure_reports_run_failed_code(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FailingRunner()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, cast(RunTracker, runner), policy=crm_prompt_policy()
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "p-boom",
            "sequence": 1,
            "commandType": "prompt",
            "payload": {"prompt": "упади"},
        }
    )
    await _drain_tasks(before)

    assert runner.calls == ["упади"]
    assert client.results[-1]["status"] == "failed"
    assert client.results[-1]["error_code"] == chat_remote_control.REASON_RUN_FAILED


async def test_stopped_run_is_reported_as_cancelled_without_error_code(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Остановка — штатный исход, а не сбой: статус cancelled и без кода причины."""
    tracker = RunTracker()
    tracker.block = asyncio.Event()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy()
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "p-stopme",
            "sequence": 1,
            "commandType": "prompt",
            "payload": {"prompt": "долгий"},
        }
    )
    await tracker.started.wait()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {"id": "stop-code", "sequence": 2, "commandType": "stop", "payload": {}}
    )
    tracker.block.set()
    await _drain_tasks(before)

    by_command = {item["command_id"]: item for item in client.results}
    assert by_command["p-stopme"]["status"] == "cancelled"
    assert by_command["p-stopme"]["error_code"] is None
    assert by_command["stop-code"]["status"] == "succeeded"
    # Публикация возвращается в ожидание ввода даже после отмены.
    stored = publications.get(coordinator._key())  # noqa: SLF001
    assert stored["state"] == "published_idle"


async def test_rc_off_closes_queued_remote_commands(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Снятая с очереди команда обязана получить терминальный статус.

    Иначе она осталась бы ``claimed`` на сервере, а отправитель в Telegram ждал
    бы ответа, которого уже никто не даст.
    """
    tracker = RunTracker()
    tracker.block = asyncio.Event()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy()
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {"id": "p-run", "sequence": 1, "commandType": "prompt", "payload": {"prompt": "первый"}}
    )
    await tracker.started.wait()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {"id": "p-queued", "sequence": 2, "commandType": "prompt", "payload": {"prompt": "второй"}}
    )
    coordinator.off()
    tracker.block.set()
    await _drain_tasks(before)

    assert tracker.calls == ["первый"]  # снятая команда не исполняется
    queued = [item for item in client.results if item["command_id"] == "p-queued"]
    assert len(queued) == 1
    assert queued[0]["status"] == "cancelled"
    assert receipts.get("p-queued")["state"] == "cancelled"


async def test_terminal_status_event_points_at_crm_session(
    rc_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ответ живёт в CRM: событие статуса отдаёт адрес ленты, а не текст.

    Без ``crmSessionId`` интерфейсу нечего показать даже после успешного turn-а —
    у control-plane текста ответа нет и быть не должно.
    """
    tracker = RunTracker()
    coordinator, client = await _published_coordinator(
        monkeypatch, tmp_path, tracker, policy=crm_prompt_policy(messages=True)
    )
    expected = crm_sync.external_session_id(
        None, coordinator._session.crm_conversation_id  # noqa: SLF001
    )

    before = asyncio.all_tasks()
    coordinator._ingest_remote_command(  # noqa: SLF001
        {
            "id": "p-answer",
            "sequence": 1,
            "commandType": "prompt",
            "payload": {"prompt": "вопрос"},
        }
    )
    await _drain_tasks(before)

    with db.conn() as connection:
        rows = connection.execute("SELECT payload FROM rc_event_outbox").fetchall()
    payloads = [json.loads(row["payload"]) for row in rows]
    statuses = [
        item
        for item in payloads
        if item.get("type") == "rc.command_status" and item.get("state") == "succeeded"
    ]
    assert statuses, "терминальное событие статуса должно быть в outbox"
    assert statuses[-1]["crmSessionId"] == expected
    # Идентификатор один и тот же в событии и в итоге команды — второго способа
    # его вычислить нет.
    assert client.results[-1]["result_summary"]["crmSessionId"] == expected


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
