"""Маршрутизация сообщения чата в опубликованную сессию /rc и обратно.

Сеть подменяется заглушками целиком: ни один тест не ходит в control-plane.
Заглушка повторяет РЕАЛЬНЫЙ контракт сервера: плоский DTO состояния команды,
отдельный маршрут чтения статуса и журнал событий курсором по ``id``, где
события устройства лежат в ``detail['payload']`` под типом с префиксом ``rc.``,
а строки аудита сервера — плоско в ``detail`` под типом без префикса.

Заглушка НЕ фильтрует журнал по ``commandId``: фильтровать обязан сам бот по
верхнеуровневому полю события, и именно это здесь проверяется.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from aiogram.filters import CommandObject

from core import config, db, herecrm_client, remote_bridge
from core.herecrm_client import HereCrmClientError
from handlers import message_remote_run, messages, remote_control, repo
from handlers.message_queue import QueuedRun
from handlers.message_state import runtime

OWNER_ID = 4242
CHAT_ID = -1001
DEVICE_ID = "device-a"
PUBLICATION_ID = "11111111-1111-1111-1111-111111111111"
CRM_CONVERSATION_ID = "conv-uuid"
COMMAND_ID = "cmd-1"
_TERMINAL_COMMAND = ("succeeded", "failed", "cancelled", "indeterminate")


# --- окружение -------------------------------------------------------------


@pytest.fixture
def bridge_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime_dir = tmp_path / ".runtime"
    for name, value in {
        "RUNTIME_DIR": runtime_dir,
        "DOWNLOADS_DIR": runtime_dir / "downloads",
        "LOGS_DIR": runtime_dir / "logs",
        "BACKUPS_DIR": runtime_dir / "backups",
        "STATE_DIR": runtime_dir / "state",
        "CLI_HOMES_DIR": runtime_dir / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_ID", OWNER_ID)
    monkeypatch.setattr(config, "ADMIN_IDS", [OWNER_ID])
    db.init()
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO accounts
               (provider, label, cli_home_path, default_model, enabled, owner_user_id, shared)
               VALUES ('claude_code', 'main', ?, 'model-a', 1, ?, 0)""",
            (str(tmp_path / "home"), OWNER_ID),
        )
    runtime.pending.clear()
    runtime.active_tasks.clear()
    yield config.DB_PATH
    runtime.pending.clear()
    runtime.active_tasks.clear()


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_remote_run, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(message_remote_run, "POLL_MAX_INTERVAL_SEC", 0.02)
    monkeypatch.setattr(message_remote_run, "FEED_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(message_remote_run, "MAX_TURN_SEC", 2.0)
    monkeypatch.setattr(message_remote_run, "POLL_GRACE_SEC", 0.02)
    monkeypatch.setattr(message_remote_run, "CLAIM_WARN_AFTER_SEC", 0.05)


# --- телеграм-заглушки -----------------------------------------------------


class FakeSent:
    def __init__(self, text: str, edits: list[str]) -> None:
        self.text = text
        self._edits = edits

    async def edit_text(self, text: str, **_: Any) -> "FakeSent":
        self._edits.append(text)
        self.text = text
        return self


class FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id
        self.type = "private"


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeMessage:
    def __init__(self, text: str = "почини сборку", user_id: int = OWNER_ID) -> None:
        self.text = text
        self.chat = FakeChat(CHAT_ID)
        self.from_user = FakeUser(user_id)
        self.message_thread_id = None
        self.message_id = 77
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.markups: list[Any] = []

    async def answer(self, text: str, **kwargs: Any) -> FakeSent:
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return FakeSent(text, self.edits)

    @property
    def said(self) -> str:
        return "\n".join(self.answers + self.edits)


class FakeBot:
    def __init__(self) -> None:
        self.documents: list[str] = []

    async def send_chat_action(self, *_: Any, **__: Any) -> None:
        return None

    async def send_document(self, *_: Any, **kwargs: Any) -> None:
        self.documents.append(str(kwargs.get("document")))

    async def send_photo(self, *_: Any, **__: Any) -> None:
        return None

    async def send_message(self, *_: Any, **__: Any) -> None:
        return None


# --- строки журнала --------------------------------------------------------


def runner_row(
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    command_id: str = COMMAND_ID,
    rc_event_id: str | None = None,
) -> dict[str, Any]:
    """Событие УСТРОЙСТВА: тип с префиксом rc., данные вложены в detail.payload."""
    return {
        "id": event_id,
        "eventType": event_type,
        "outcome": "success",
        "commandId": command_id,
        "deviceId": DEVICE_ID,
        "createdAt": time.time(),
        "detail": {"rcEventId": rc_event_id or f"evt-{event_id}", "payload": payload},
    }


def audit_row(event_id: str, event_type: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Строка аудита СЕРВЕРА: тип без префикса, поля плоско в detail."""
    return {
        "id": event_id,
        "eventType": event_type,
        "outcome": "success",
        "commandId": COMMAND_ID,
        "deviceId": DEVICE_ID,
        "createdAt": time.time(),
        "detail": detail,
    }


# --- заглушка control-plane ------------------------------------------------


class FakeControlPlane:
    """Минимальная модель четырёх маршрутов ``hereassistant-sync/rc/*``."""

    def __init__(self, *, publications: list[dict[str, Any]]) -> None:
        self.publications = publications
        self.commands: list[dict[str, Any]] = []
        # Сценарий статусов команды: по одному на каждое чтение состояния.
        self.statuses: list[str] = ["succeeded"]
        self.error_code: str | None = None
        self.result_summary: dict[str, Any] | None = None
        self.answer_text: str | None = None
        self.events: list[dict[str, Any]] = []
        self.expires_in: float | None = 60.0
        self.fail_publications: str | None = None
        self.fail_command: str | None = None
        self.fail_state: str | None = None
        self.create_calls = 0
        self.state_reads = 0
        self.event_cursors: list[str | None] = []
        self.event_filters: list[str | None] = []
        self.publication_filters: list[str] = []

    def _status_for(self, record: dict[str, Any]) -> str:
        step = record["polls"]
        record["polls"] = step + 1
        if step < len(self.statuses):
            return self.statuses[step]
        return self.statuses[-1] if self.statuses else "pending"

    def _state_dto(self, record: dict[str, Any], *, created: bool) -> dict[str, Any]:
        """Плоский HereassistantRcCommandStateDto — никакой обёртки command."""
        status = self._status_for(record)
        dto: dict[str, Any] = {
            "commandId": record["id"],
            "publicationId": record["publication_id"],
            "sequence": record["sequence"],
            "commandType": record["command_type"],
            "status": status,
            "created": created,
            "errorCode": None,
            "resultSummary": None,
            "createdAt": record["created_at"],
            "expiresAt": (record["created_at"] + self.expires_in) if self.expires_in else None,
            "claimedAt": None,
            "startedAt": None,
            "finishedAt": None,
        }
        if status in _TERMINAL_COMMAND:
            dto["errorCode"] = self.error_code
            dto["resultSummary"] = dict(self.result_summary) if self.result_summary else None
        return dto

    async def rc_publications(self, *, state: str = "live", device_id: str | None = None) -> Any:
        self.publication_filters.append(state)
        if self.fail_publications:
            raise HereCrmClientError(self.fail_publications, 502)
        rows = list(self.publications)
        if device_id:
            rows = [row for row in rows if row.get("deviceId") == device_id]
        if state == "live":
            rows = [
                row
                for row in rows
                if row.get("state") not in remote_bridge.TERMINAL_PUBLICATION_STATES
            ]
        return {"items": rows}

    async def rc_create_command(
        self,
        publication_id: str,
        *,
        command_type: str,
        payload: Any = None,
        idempotency_key: str,
    ) -> Any:
        self.create_calls += 1
        if self.fail_command:
            raise HereCrmClientError(self.fail_command, 409)
        known = next(
            (item for item in self.commands if item["idempotency_key"] == idempotency_key),
            None,
        )
        if known is not None:
            # Повтор тем же ключом второй команды не создаёт.
            return self._state_dto(known, created=False)
        record = {
            "id": f"cmd-{len(self.commands) + 1}",
            "publication_id": publication_id,
            "command_type": command_type,
            "payload": dict(payload) if payload else {},
            "idempotency_key": idempotency_key,
            "sequence": len(self.commands) + 1,
            "created_at": time.time(),
            "polls": 0,
        }
        self.commands.append(record)
        return self._state_dto(record, created=True)

    async def rc_command_state(self, publication_id: str, command_id: str) -> Any:
        self.state_reads += 1
        if self.fail_state:
            raise HereCrmClientError(self.fail_state, 409)
        record = next((item for item in self.commands if item["id"] == command_id), None)
        if record is None or record["publication_id"] != publication_id:
            raise HereCrmClientError("rc_not_found", 404)
        return self._state_dto(record, created=True)

    async def rc_events(
        self,
        publication_id: str,
        *,
        after_id: str | None = None,
        limit: int = 100,
        command_id: str | None = None,
    ) -> Any:
        self.event_cursors.append(after_id)
        self.event_filters.append(command_id)
        cursor = int(after_id) if after_id else 0
        # Сервер отдаёт СВОЙ журнал целиком: фильтровать чужие команды обязан бот.
        rows = [row for row in self.events if int(row["id"]) > cursor][:limit]
        return {"items": rows, "nextCursor": rows[-1]["id"] if rows else None}

    async def conversations(self, **_: Any) -> Any:
        return [{"id": CRM_CONVERSATION_ID, "providerSessionId": "sess-1"}]

    async def feed(self, conversation_id: str, **_: Any) -> Any:
        if not self.answer_text:
            return {"items": [], "hasMore": False}
        return {
            "items": [
                {
                    "kind": "message",
                    "message": {
                        "id": "m1",
                        "role": "assistant",
                        "content": self.answer_text,
                        "createdAt": time.time() + 1,
                    },
                }
            ],
            "hasMore": False,
        }

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "FakeControlPlane":
        monkeypatch.setattr(herecrm_client, "configured", lambda: True)
        monkeypatch.setattr(herecrm_client, "rc_configured", lambda: True)
        for name in (
            "rc_publications",
            "rc_create_command",
            "rc_command_state",
            "rc_events",
            "conversations",
            "feed",
        ):
            monkeypatch.setattr(herecrm_client, name, getattr(self, name))
        return self


def publication_row(**overrides: Any) -> dict[str, Any]:
    now = time.time()
    row: dict[str, Any] = {
        "id": PUBLICATION_ID,
        "publicId": "pub-1",
        "state": "published_idle",
        "deviceId": DEVICE_ID,
        "deviceName": "MacBook",
        "devicePlatform": "darwin",
        "deviceStatus": "active",
        "conversationId": CRM_CONVERSATION_ID,
        "privacyMode": "crm",
        "capabilities": {"remotePrompt": True, "stop": True},
        "publishedAt": now - 300,
        "lastHeartbeatAt": now - 3,
        "expiresAt": now + 3600,
        "online": True,
        "heartbeatAgeSec": 3,
    }
    row.update(overrides)
    return row


def bind(
    device_id: str | None = DEVICE_ID,
    name: str | None = "MacBook",
    publication_id: str | None = PUBLICATION_ID,
    conversation_id: str | None = CRM_CONVERSATION_ID,
) -> Any:
    conv = repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    repo.set_remote_device(int(conv["id"]), device_id, name, publication_id, conversation_id)
    return repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)


def run_for(message: FakeMessage, text: str = "почини сборку") -> QueuedRun:
    conv = repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    return QueuedRun(conv=conv, text=text, message=message, main_attachment=None, attachments=[])


def binding_row() -> Any:
    return repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)


# --- маршрутизация ---------------------------------------------------------


async def test_bound_thread_sends_message_to_device(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["pending", "running", "succeeded"]
    plane.result_summary = {"crmSessionId": "sess-1"}
    plane.answer_text = "сборка починена"
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert len(plane.commands) == 1
    command = plane.commands[0]
    assert command["command_type"] == "prompt"
    assert command["publication_id"] == PUBLICATION_ID
    # В payload уезжает ТОЛЬКО текст запроса: ни chat_id, ни username, ни заголовок.
    assert command["payload"] == {"prompt": "почини сборку"}
    assert command["idempotency_key"] == f"ha-tg:{CHAT_ID}:0:77"
    assert "сборка починена" in message.said


async def test_status_is_read_by_its_own_route_not_by_second_post(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Опрос — это ЧТЕНИЕ состояния команды. Повторный POST тем же ключом как
    # способ опроса запрещён: это запись, и на неё нельзя опираться.
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["pending", "pending", "pending", "succeeded"]
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert plane.create_calls == 1
    assert len(plane.commands) == 1
    assert plane.state_reads == 3


async def test_publications_are_requested_with_all_states(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Закрытую публикацию нужно ВИДЕТЬ, иначе вместо «публикация закрыта»
    # человек получит расплывчатое «нет публикаций».
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    conv = bind()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(FakeMessage()))

    assert plane.publication_filters == ["all"]


async def test_answer_is_not_invented_without_session_id(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["succeeded"]
    plane.answer_text = "чужой ответ из другой сессии"
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert "чужой ответ" not in message.said
    assert "Готово" in message.said


async def test_failure_status_is_reported_with_reason(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["failed"]
    plane.error_code = "PRIVACY_DENIED"
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert "ошибке" in message.said
    assert "PRIVACY_DENIED" in message.said
    # Код без расшифровки человеку ничего не говорит.
    assert "запрещает удалённые промпты" in message.said


async def test_approval_refusal_is_told_apart_from_privacy(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["failed"]
    plane.error_code = "APPROVAL_LOCAL_ONLY"
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert "APPROVAL_LOCAL_ONLY" in message.said
    assert "только за компьютером" in message.said
    assert "запрещает удалённые промпты" not in message.said


# --- потолок ожидания ------------------------------------------------------


async def test_turn_stops_at_command_expiry(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Команда живёт мгновение и никто её не забрал: turn обязан закончиться
    # сразу после её срока, а не висеть до собственного потолка.
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["pending"]
    plane.expires_in = 0.05
    conv = bind()
    message = FakeMessage()

    started = time.time()
    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert time.time() - started < 1.5
    assert "просрочена" in message.said


async def test_turn_stops_at_own_ceiling_without_server_expiry(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Сервер не сообщил срок команды — ждём не дольше своего потолка, иначе
    # опрос держал бы признак занятости процесса неограниченно долго.
    monkeypatch.setattr(message_remote_run, "MAX_TURN_SEC", 0.3)
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["running"]
    plane.expires_in = None
    conv = bind()
    message = FakeMessage()

    started = time.time()
    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert time.time() - started < 1.5
    assert "Жду слишком долго" in message.said
    assert "/rc status" in message.said


async def test_status_event_alone_does_not_finish_turn(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Событие ``rc.command_status`` — подсказка, а не итог turn-а.

    Источник истины — строка команды. Если кто-то начнёт закрывать turn по
    событию, тест увидит «Готово» вместо честного потолка ожидания.
    """
    monkeypatch.setattr(message_remote_run, "MAX_TURN_SEC", 0.3)
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["running"]
    plane.expires_in = None
    plane.events = [runner_row("1", "rc.command_status", {"state": "succeeded"})]
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert "Готово" not in message.said
    assert "Жду слишком долго" in message.said


# --- журнал событий: реальная форма ---------------------------------------


async def test_event_feed_reads_canonical_shapes(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.events = [
        runner_row("1", "rc.progress", {"text": " почти готово "}),
        runner_row("2", "rc.tool_call", {"tool": "Edit", "status": "ok", "path": "core/db.py"}),
        runner_row("3", "rc.diff_summary", {"filesChanged": 2, "insertions": 9, "deletions": 1}),
        runner_row("4", "rc.command_status", {"state": "succeeded"}),
        audit_row("5", "command_claimed", {"runnerEpoch": 3}),
    ]
    feed = message_remote_run._EventFeed(publication_id=PUBLICATION_ID, command_id=COMMAND_ID)

    await feed.pull()

    assert feed.note == "📄 Правки: файлов 2, +9/−1"
    assert {"status": "ok", "desc": "Edit · core/db.py"} in feed.steps
    assert feed.hint_state == "succeeded"
    assert feed.cursor == "5"
    # Бот подсказывает серверу свой commandId, но полагается на своё сравнение.
    assert plane.event_filters == [COMMAND_ID]
    assert plane.event_cursors == [None]


async def test_event_feed_ignores_invented_shape_and_foreign_command(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выдуманная форма события не должна двигать turn ни на шаг.

    Историческая ошибка: читать ``detail['status']`` у события с префиксом
    ``rc.`` и считать типом ``command_status`` без префикса. Обе формы здесь
    присутствуют — и обе обязаны быть проигнорированы читателем.
    """
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.events = [
        # Выдуманная форма: статус на верхнем уровне detail у события раннера.
        {
            "id": "1",
            "eventType": "rc.command_status",
            "commandId": COMMAND_ID,
            "detail": {"rcEventId": "evt-1", "status": "succeeded"},
        },
        # Чужая команда той же публикации.
        runner_row("2", "rc.progress", {"text": "чужой прогресс"}, command_id="cmd-999"),
        # Строка публикации: commandId отсутствует вовсе.
        {"id": "3", "eventType": "publication_created", "detail": {"privacyMode": "crm"}},
    ]
    feed = message_remote_run._EventFeed(publication_id=PUBLICATION_ID, command_id=COMMAND_ID)

    await feed.pull()

    assert feed.hint_state is None
    assert feed.note == ""
    assert feed.steps == []
    assert feed.cursor == "3"


async def test_event_feed_deduplicates_by_rc_event_id(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    step = {"tool": "Read", "status": "ok", "path": "bot.py"}
    plane.events = [
        runner_row("1", "rc.tool_call", step, rc_event_id="same"),
        runner_row("2", "rc.tool_call", step, rc_event_id="same"),
    ]
    feed = message_remote_run._EventFeed(publication_id=PUBLICATION_ID, command_id=COMMAND_ID)

    await feed.pull()

    assert len(feed.steps) == 1


async def test_event_feed_keeps_cursor_on_empty_page(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.events = [runner_row("9", "rc.progress", {"text": "шаг"})]
    feed = message_remote_run._EventFeed(publication_id=PUBLICATION_ID, command_id=COMMAND_ID)

    await feed.pull()
    assert feed.cursor == "9"
    await feed.pull()
    assert feed.cursor == "9"
    assert plane.event_cursors == [None, "9"]


async def test_approval_request_is_reported_once(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["pending", "pending", "succeeded"]
    plane.events = [runner_row("1", "rc.approval_required", {"tool": "Bash", "reason": "rm -rf"})]
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    said = "\n".join(message.answers)
    assert said.count("просит подтверждение") == 1
    assert "только за компьютером" in said


# --- отказы вместо ложного успеха ------------------------------------------


async def test_offline_device_refuses_before_any_post(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = publication_row(lastHeartbeatAt=time.time() - 600, online=False, heartbeatAgeSec=600)
    plane = FakeControlPlane(publications=[stale]).install(monkeypatch)
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert plane.commands == []
    assert "не выходит на связь" in message.said
    # Офлайн — временное состояние: привязку не сносим.
    assert binding_row()["rc_device_id"] == DEVICE_ID
    assert binding_row()["rc_conversation_id"] == CRM_CONVERSATION_ID


async def test_closed_publication_detaches_thread(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row(state="closed")]).install(monkeypatch)
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert plane.commands == []
    assert "Привязка треда к сессии устройства снята" in message.said
    assert binding_row()["rc_device_id"] is None
    assert binding_row()["rc_publication_id"] is None
    assert binding_row()["rc_conversation_id"] is None


async def test_other_session_of_same_device_is_never_used_silently(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Живая публикация есть, но это ДРУГОЙ проект того же компьютера: отправлять
    # туда запрос нельзя — у него свой контекст и своя политика приватности.
    other = publication_row(id="pub-2", conversationId="conv-другая")
    plane = FakeControlPlane(publications=[other]).install(monkeypatch)
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert plane.commands == []
    assert "выбери устройство и сессию заново" in message.said
    assert binding_row()["rc_device_id"] is None


async def test_ambiguous_device_binding_asks_to_pick_again(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Старая привязка без сессии CRM, а у машины две живые публикации.
    rows = [
        publication_row(id="pub-1", conversationId=None),
        publication_row(id="pub-2", conversationId=None),
    ]
    plane = FakeControlPlane(publications=rows).install(monkeypatch)
    conv = bind(publication_id=None, conversation_id=None)
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert plane.commands == []
    assert "выбери устройство и сессию заново" in message.said


async def test_server_conflict_detaches_thread(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.fail_command = "rc_publication_closed"
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert binding_row()["rc_device_id"] is None
    assert "публикация закрыта" in message.said.lower()


async def test_publication_closed_during_poll_ends_turn(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    plane.statuses = ["pending"]
    plane.fail_state = "rc_publication_closed"
    conv = bind()
    message = FakeMessage()

    started = time.time()
    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert time.time() - started < 1.5
    assert "просрочена" in message.said


async def test_private_project_capabilities_block_prompt(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = publication_row(capabilities={}, privacyMode="private")
    plane = FakeControlPlane(publications=[private]).install(monkeypatch)
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert plane.commands == []
    assert "не принимает удалённые промпты" in message.said


async def test_attachments_are_refused_before_network(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    conv = bind()
    message = FakeMessage()
    attachment = tmp_path / "secret.txt"
    attachment.write_text("данные проекта", encoding="utf-8")
    run = QueuedRun(
        conv=conv,
        text="посмотри файл",
        message=message,
        main_attachment=attachment,
        attachments=[attachment],
    )

    await message_remote_run.run_remote_turn(FakeBot(), conv, run)

    assert plane.commands == []
    assert "вложения пока не поддерживаются" in message.said


async def test_missing_crm_config_refuses(bridge_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    monkeypatch.setattr(herecrm_client, "rc_configured", lambda: False)
    conv = bind()
    message = FakeMessage()

    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(message))

    assert "не настроен" in message.said


async def test_repeated_message_does_not_start_second_run(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    conv = bind()

    first = FakeMessage()
    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(first))
    second = FakeMessage()
    await message_remote_run.run_remote_turn(FakeBot(), conv, run_for(second))

    # Тот же message_id → тот же ключ → второго запуска агента на устройстве нет.
    assert len(plane.commands) == 1
    assert "уже был отправлен раньше" in second.said


# --- развилка в общем обработчике сообщений --------------------------------


async def test_flush_pending_routes_to_device_when_bound(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_calls: list[Any] = []
    local_calls: list[Any] = []
    monkeypatch.setattr(
        messages, "start_remote_turn", lambda *args: remote_calls.append(args) or None
    )
    monkeypatch.setattr(messages, "_start_run", lambda *args: local_calls.append(args) or None)
    bind()
    message = FakeMessage()
    key = (OWNER_ID, CHAT_ID, 0)
    runtime.pending[key] = {"last_message": message, "texts": ["сделай"], "attachments": []}

    await messages._flush_pending(FakeBot(), key)

    assert len(remote_calls) == 1
    assert local_calls == []


async def test_flush_pending_keeps_server_session_without_binding(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_calls: list[Any] = []
    local_calls: list[Any] = []
    monkeypatch.setattr(
        messages, "start_remote_turn", lambda *args: remote_calls.append(args) or None
    )
    monkeypatch.setattr(messages, "_start_run", lambda *args: local_calls.append(args) or None)
    repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    message = FakeMessage()
    key = (OWNER_ID, CHAT_ID, 0)
    runtime.pending[key] = {"last_message": message, "texts": ["сделай"], "attachments": []}

    await messages._flush_pending(FakeBot(), key)

    assert remote_calls == []
    assert len(local_calls) == 1


async def test_flush_pending_ignores_binding_for_non_owner(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stranger = 999
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO accounts
               (provider, label, cli_home_path, default_model, enabled, owner_user_id, shared)
               VALUES ('claude_code', 'guest', '/tmp/guest', 'model-a', 1, ?, 0)""",
            (stranger,),
        )
    conv = repo.get_or_create_conv(CHAT_ID, 0, stranger)
    repo.set_remote_device(int(conv["id"]), DEVICE_ID, "MacBook", PUBLICATION_ID, "conv-x")

    remote_calls: list[Any] = []
    local_calls: list[Any] = []
    monkeypatch.setattr(
        messages, "start_remote_turn", lambda *args: remote_calls.append(args) or None
    )
    monkeypatch.setattr(messages, "_start_run", lambda *args: local_calls.append(args) or None)
    message = FakeMessage(user_id=stranger)
    key = (stranger, CHAT_ID, 0)
    runtime.pending[key] = {"last_message": message, "texts": ["сделай"], "attachments": []}

    await messages._flush_pending(FakeBot(), key)

    assert remote_calls == []
    assert len(local_calls) == 1


# --- переключение цели командой /rc ----------------------------------------


def command(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="rc", args=args)


async def test_rc_off_returns_thread_to_server_session(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    bind()
    message = FakeMessage()

    await remote_control.cmd_rc(message, command("off"))

    assert binding_row()["rc_device_id"] is None
    assert binding_row()["rc_publication_id"] is None
    assert "снята" in message.said


async def test_rc_status_shows_current_target(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    bind()
    message = FakeMessage()

    await remote_control.cmd_rc(message, command("status"))

    assert "MacBook" in message.said
    assert "промпты" in message.said


async def test_rc_status_reports_moved_session(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = publication_row(id="pub-2", conversationId="conv-другая")
    FakeControlPlane(publications=[other]).install(monkeypatch)
    bind()
    message = FakeMessage()

    await remote_control.cmd_rc(message, command("status"))

    assert "выбери устройство и сессию заново" in message.said


async def test_rc_status_without_binding_names_server_session(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    message = FakeMessage()

    await remote_control.cmd_rc(message, command("status"))

    assert "серверная сессия" in message.said


async def test_rc_picker_offers_publications_not_devices(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        publication_row(),
        # Второй проект того же компьютера — отдельная кнопка, не склейка.
        publication_row(id="pub-2", conversationId="conv-2"),
        publication_row(
            id="pub-dead",
            deviceId="device-b",
            deviceName="Сервер",
            conversationId="conv-3",
            lastHeartbeatAt=time.time() - 999,
            online=False,
            heartbeatAgeSec=999,
        ),
    ]
    FakeControlPlane(publications=rows).install(monkeypatch)
    repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    message = FakeMessage()

    await remote_control.cmd_rc(message, command(None))

    markup = message.markups[-1]
    assert markup is not None
    buttons = [button for row in markup.inline_keyboard for button in row]
    # Кнопка адресует ПУБЛИКАЦИЮ: id устройства целью не является.
    assert sorted(button.callback_data for button in buttons) == [
        f"rc:use:{PUBLICATION_ID}",
        "rc:use:pub-2",
    ]


async def test_rc_pick_saves_publication_and_session(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    message = FakeMessage()

    ack = await remote_control.bind_publication(message, OWNER_ID, PUBLICATION_ID)  # type: ignore[arg-type]

    row = binding_row()
    assert row["rc_device_id"] == DEVICE_ID
    assert row["rc_publication_id"] == PUBLICATION_ID
    assert row["rc_conversation_id"] == CRM_CONVERSATION_ID
    assert ack == "Готово"


async def test_rc_pick_refuses_stale_publication(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeControlPlane(publications=[publication_row(state="closed")]).install(monkeypatch)
    repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    message = FakeMessage()

    ack = await remote_control.bind_publication(message, OWNER_ID, PUBLICATION_ID)  # type: ignore[arg-type]

    assert ack == "Публикация уже недоступна"
    assert binding_row()["rc_device_id"] is None
    assert "выбери устройство и сессию заново" in message.said


async def test_rc_picker_reports_missing_publication(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeControlPlane(publications=[]).install(monkeypatch)
    repo.get_or_create_conv(CHAT_ID, 0, OWNER_ID)
    message = FakeMessage()

    await remote_control.cmd_rc(message, command(None))

    assert "Нет опубликованных сессий" in message.said
    assert message.markups[-1] is None


async def test_rc_is_silent_for_non_owner(bridge_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    message = FakeMessage(user_id=999)

    await remote_control.cmd_rc(message, command(None))

    assert message.answers == []


async def test_rc_stop_requires_stop_capability(
    bridge_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = FakeControlPlane(
        publications=[publication_row(capabilities={"remotePrompt": True})]
    ).install(monkeypatch)
    bind()
    message = FakeMessage()

    await remote_control.cmd_rc(message, command("stop"))

    assert plane.commands == []
    assert "не разрешает удалённую остановку" in message.said


async def test_rc_stop_sends_stop_command(bridge_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plane = FakeControlPlane(publications=[publication_row()]).install(monkeypatch)
    bind()
    message = FakeMessage()

    await remote_control.cmd_rc(message, command("stop"))

    assert [item["command_type"] for item in plane.commands] == ["stop"]
    assert plane.commands[0]["idempotency_key"] == f"ha-tg-stop:{CHAT_ID}:0:77"
    # Пустой payload в тело не уезжает вовсе: маршрут отклоняет лишние поля.
    assert plane.commands[0]["payload"] == {}


async def test_binding_survives_and_switches(bridge_db: Path) -> None:
    conv = bind()
    assert remote_bridge.conversation_binding(conv) == remote_bridge.Binding(
        DEVICE_ID, "MacBook", PUBLICATION_ID, CRM_CONVERSATION_ID
    )
    switched = bind("device-b", "Сервер", "pub-2", "conv-2")
    assert remote_bridge.conversation_binding(switched) == remote_bridge.Binding(
        "device-b", "Сервер", "pub-2", "conv-2"
    )
    cleared = bind(None, None, None, None)
    assert remote_bridge.conversation_binding(cleared) == remote_bridge.Binding()
