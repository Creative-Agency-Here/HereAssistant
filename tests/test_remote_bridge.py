"""Чистый слой моста Telegram → /rc: выбор цели, контракт событий, отказы.

Главное, что здесь зацементировано, — РЕАЛЬНАЯ форма журнала публикации. У
события устройства состояние лежит в ``detail['payload']['state']``, а у строки
аудита сервера — в ``detail['status']``. Любая попытка вернуть выдуманную форму
(«статус на верхнем уровне detail у события раннера») обязана краснить тесты.
"""

from __future__ import annotations

from typing import Any

from core import remote_bridge
from core.remote_control.config import OFFLINE_AFTER_SEC

NOW = 1_800_000_000.0
COMMAND_ID = "cmd-1"


def publication(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "publicId": "pub-1",
        "state": "published_idle",
        "deviceId": "device-a",
        "deviceName": "MacBook",
        "devicePlatform": "darwin",
        "deviceStatus": "active",
        "conversationId": "conv-1",
        "privacyMode": "crm",
        "capabilities": {"remotePrompt": True, "stop": True},
        "publishedAt": NOW - 600,
        "lastHeartbeatAt": NOW - 5,
        "expiresAt": NOW + 3600,
        "online": True,
        "heartbeatAgeSec": 5,
    }
    payload.update(overrides)
    return payload


def parsed(**overrides: Any) -> remote_bridge.Publication:
    result = remote_bridge.parse_publication(publication(**overrides))
    assert result is not None
    return result


def audit_event(**overrides: Any) -> dict[str, Any]:
    """Строка аудита СЕРВЕРА: тип без префикса, поля прямо в detail."""
    row: dict[str, Any] = {
        "id": "101",
        "eventType": "command_status",
        "outcome": "success",
        "commandId": COMMAND_ID,
        "deviceId": "device-a",
        "createdAt": NOW,
        "detail": {"status": "failed", "errorCode": "PRIVACY_DENIED"},
    }
    row.update(overrides)
    return row


def runner_event(event_type: str, payload: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Событие РАННЕРА: тип с префиксом rc., данные вложены в detail.payload."""
    row: dict[str, Any] = {
        "id": "102",
        "eventType": event_type,
        "outcome": "success",
        "commandId": COMMAND_ID,
        "deviceId": "device-a",
        "createdAt": NOW,
        "detail": {"rcEventId": "evt-1", "payload": payload},
    }
    row.update(overrides)
    return row


# --- публикации ------------------------------------------------------------


def test_parse_publication_reads_iso_and_epoch() -> None:
    iso = remote_bridge.parse_publication(
        publication(lastHeartbeatAt="2027-01-02T03:04:05Z", publishedAt=NOW * 1000)
    )
    assert iso is not None
    assert iso.last_heartbeat_at == 1798859045.0
    # Миллисекунды нормализуются в секунды, а не превращаются в 2085 год.
    assert abs(iso.published_at - NOW) < 1.0


def test_parse_publication_drops_rows_without_device() -> None:
    assert remote_bridge.parse_publication(publication(deviceId="")) is None
    assert remote_bridge.parse_publication({"id": "x"}) is None
    assert remote_bridge.parse_publications({"items": [publication()]})[0].device_id == "device-a"


def test_publication_carries_device_identity_from_response() -> None:
    # Имя, платформа и состояние устройства приходят в ЭТОМ ЖЕ ответе: у сессий
    # HereAssistant device_id в ленте диалогов не заполнен, и карта имён пуста.
    item = parsed()
    assert item.device_name == "MacBook"
    assert item.device_platform == "darwin"
    assert item.device_status == "active"
    assert item.conversation_id == "conv-1"


def test_publication_name_falls_back_to_map_then_to_neutral_label() -> None:
    anonymous = remote_bridge.parse_publication(publication(deviceName=""))
    assert anonymous is not None
    assert anonymous.device_name == "устройство"
    mapped = remote_bridge.parse_publication(publication(deviceName=""), {"device-a": "Сервер"})
    assert mapped is not None
    assert mapped.device_name == "Сервер"


def test_server_offline_flag_wins_over_local_clock() -> None:
    # Сервер посчитал свежесть по своим часам: спорить с его «offline» нельзя.
    stale = parsed(online=False)
    assert remote_bridge.is_online(stale, NOW) is False


def test_heartbeat_age_uses_server_number_without_timestamp() -> None:
    item = parsed(lastHeartbeatAt=None, heartbeatAgeSec=7)
    assert remote_bridge.heartbeat_age(item, NOW) == 7.0
    assert remote_bridge.is_online(item, NOW) is True


# --- выбор цели ------------------------------------------------------------


def test_select_target_matches_publication_by_crm_session() -> None:
    selection = remote_bridge.select_target(
        [parsed()], "device-a", now=NOW, conversation_id="conv-1"
    )
    assert selection.refusal is None
    assert selection.publication is not None
    assert selection.publication.conversation_id == "conv-1"


def test_select_target_refuses_when_session_publication_changed() -> None:
    # Та же машина, но другой проект: молчаливая подмена цели запрещена.
    other_project = parsed(id="pub-2", conversationId="conv-2")
    selection = remote_bridge.select_target(
        [other_project], "device-a", now=NOW, conversation_id="conv-1"
    )
    assert selection.publication is None
    assert selection.refusal == remote_bridge.SESSION_MOVED
    assert "выбери устройство и сессию заново" in remote_bridge.refusal_text(selection.refusal)


def test_select_target_refuses_when_session_moved_to_other_machine() -> None:
    moved = parsed(deviceId="device-b")
    selection = remote_bridge.select_target(
        [moved], "device-a", now=NOW, conversation_id="conv-1"
    )
    assert selection.refusal == remote_bridge.SESSION_MOVED


def test_select_target_refuses_ambiguous_device_without_session() -> None:
    # Привязка без сессии CRM и две живые публикации: «самая свежая» однажды
    # окажется чужим проектом, поэтому просим выбрать заново.
    first = parsed(id="pub-1", conversationId=None, publishedAt=NOW - 900)
    second = parsed(id="pub-2", conversationId=None, publishedAt=NOW - 60)
    selection = remote_bridge.select_target([first, second], "device-a", now=NOW)
    assert selection.refusal == remote_bridge.SESSION_MOVED


def test_select_target_allows_single_publication_without_session() -> None:
    only = parsed(conversationId=None)
    selection = remote_bridge.select_target([only], "device-a", now=NOW)
    assert selection.publication is not None
    assert selection.publication.id == only.id


def test_select_target_without_binding_asks_to_enable_mode() -> None:
    selection = remote_bridge.select_target([parsed()], None, now=NOW)
    assert selection.refusal == remote_bridge.NO_DEVICE


def test_select_target_reports_foreign_device_as_missing_publication() -> None:
    selection = remote_bridge.select_target([parsed()], "device-b", now=NOW)
    assert selection.refusal == remote_bridge.NO_PUBLICATION


def test_select_target_detects_stale_heartbeat() -> None:
    stale = parsed(lastHeartbeatAt=NOW - OFFLINE_AFTER_SEC - 1, online=None, heartbeatAgeSec=None)
    selection = remote_bridge.select_target(
        [stale], "device-a", now=NOW, conversation_id="conv-1"
    )
    assert selection.refusal == remote_bridge.DEVICE_OFFLINE
    assert "не выходит на связь" in remote_bridge.refusal_text(selection.refusal)


def test_select_target_detects_missing_heartbeat() -> None:
    silent = parsed(lastHeartbeatAt=None, online=None, heartbeatAgeSec=None)
    assert remote_bridge.select_target([silent], "device-a", now=NOW).refusal == (
        remote_bridge.DEVICE_OFFLINE
    )


def test_select_target_detects_closed_and_expired_publication() -> None:
    closed = parsed(state="closed", conversationId=None)
    assert remote_bridge.select_target([closed], "device-a", now=NOW).refusal == (
        remote_bridge.PUBLICATION_CLOSED
    )
    expired = parsed(expiresAt=NOW - 1, conversationId=None)
    assert remote_bridge.select_target([expired], "device-a", now=NOW).refusal == (
        remote_bridge.PUBLICATION_CLOSED
    )
    # Привязка к сессии: закрытая публикация этой сессии = её надо выбрать заново.
    assert remote_bridge.select_target(
        [parsed(state="closed")], "device-a", now=NOW, conversation_id="conv-1"
    ).refusal == remote_bridge.SESSION_MOVED


def test_capabilities_are_default_deny() -> None:
    denied = parsed(capabilities={})
    assert remote_bridge.select_target(
        [denied], "device-a", now=NOW, conversation_id="conv-1"
    ).refusal == remote_bridge.PROMPT_DENIED
    # Строковое "true" разрешением не считается: default deny означает именно True.
    fake = parsed(capabilities={"remotePrompt": "true"})
    assert remote_bridge.select_target(
        [fake], "device-a", now=NOW, conversation_id="conv-1"
    ).refusal == remote_bridge.PROMPT_DENIED


def test_stop_capability_checked_separately() -> None:
    no_stop = parsed(capabilities={"remotePrompt": True})
    selection = remote_bridge.select_target(
        [no_stop], "device-a", now=NOW, capability="stop", conversation_id="conv-1"
    )
    assert selection.refusal == remote_bridge.STOP_DENIED


def test_live_publications_are_listed_per_session() -> None:
    targets = remote_bridge.live_publications(
        [
            parsed(id="a-old", publishedAt=NOW - 900),
            parsed(id="a-new", publishedAt=NOW - 30),
            parsed(id="b", conversationId="conv-2", publishedAt=NOW - 120),
            parsed(id="dead", conversationId="conv-3", state="closed"),
            parsed(id="silent", conversationId="conv-4", lastHeartbeatAt=NOW - 999, online=False),
        ],
        NOW,
    )
    # Одна сессия — одна кнопка (свежая публикация), закрытые и молчащие скрыты.
    assert [item.id for item in targets] == ["a-new", "b"]


def test_find_publication_by_id() -> None:
    first, second = parsed(id="pub-1"), parsed(id="pub-2", conversationId="conv-2")
    assert remote_bridge.find_publication([first, second], "pub-2") is second
    assert remote_bridge.find_publication([first, second], "нет такой") is None


# --- отказы и коды причин --------------------------------------------------


def test_unbind_only_for_terminal_refusals() -> None:
    assert remote_bridge.should_unbind("rc_forbidden")
    assert remote_bridge.should_unbind(remote_bridge.PUBLICATION_CLOSED)
    assert remote_bridge.should_unbind(remote_bridge.SESSION_MOVED)
    # Офлайн — временное состояние, привязку сносить нельзя.
    assert not remote_bridge.should_unbind(remote_bridge.DEVICE_OFFLINE)
    assert not remote_bridge.should_unbind("rc_unavailable")


def test_every_refusal_has_text() -> None:
    codes = [
        remote_bridge.NO_DEVICE,
        remote_bridge.NO_PUBLICATION,
        remote_bridge.SESSION_MOVED,
        remote_bridge.DEVICE_OFFLINE,
        remote_bridge.PUBLICATION_CLOSED,
        remote_bridge.PROMPT_DENIED,
        remote_bridge.STOP_DENIED,
        remote_bridge.NOT_CONFIGURED,
        remote_bridge.ATTACHMENTS_UNSUPPORTED,
        remote_bridge.COMMAND_EXPIRED,
        "rc_forbidden",
        "rc_not_found",
        "rc_publication_closed",
        "rc_unavailable",
        "rc_not_configured",
    ]
    for code in codes:
        assert remote_bridge.refusal_text(code) != remote_bridge.refusal_text("нет такого кода")


def test_privacy_and_approval_refusals_are_told_apart() -> None:
    # Человек должен видеть разницу между «проект запретил промпты» и
    # «подтверждение можно дать только за компьютером».
    privacy = remote_bridge.error_code_text("PRIVACY_DENIED")
    approval = remote_bridge.error_code_text("APPROVAL_LOCAL_ONLY")
    assert privacy and approval and privacy != approval
    for code in (
        "RUN_FAILED",
        "GIT_ACTION_FAILED",
        "PAYLOAD_MISMATCH",
        "UNKNOWN_COMMAND_TYPE",
        "RESULT_UNKNOWN",
        "DEVICE_OFFLINE",
        "PUBLICATION_CLOSED",
        "CAPABILITY_UNAVAILABLE",
        "IDEMPOTENCY_KEY_REQUIRED",
        "IDEMPOTENCY_KEY_INVALID",
    ):
        assert remote_bridge.error_code_text(code)
    assert remote_bridge.error_code_text(None) is None
    assert remote_bridge.error_code_text("НЕТ_ТАКОГО") is None


def test_idempotency_key_is_deterministic_per_message() -> None:
    first = remote_bridge.idempotency_key("prompt", -100, 7, 42)
    assert first == "ha-tg:-100:7:42"
    assert first == remote_bridge.idempotency_key("prompt", -100, 7, 42)
    assert remote_bridge.idempotency_key("stop", -100, 7, 42) == "ha-tg-stop:-100:7:42"


# --- привязка треда --------------------------------------------------------


def test_conversation_binding_reads_all_four_columns() -> None:
    binding = remote_bridge.conversation_binding(
        {
            "rc_device_id": " a ",
            "rc_device_name": "Mac",
            "rc_publication_id": "pub-1",
            "rc_conversation_id": "conv-1",
        }
    )
    assert binding == remote_bridge.Binding("a", "Mac", "pub-1", "conv-1")
    assert binding.active is True
    assert binding.label == "Mac"


def test_conversation_binding_tolerates_legacy_rows() -> None:
    # Старая строка БД без новых колонок — не ошибка: режим просто выключен.
    assert remote_bridge.conversation_binding({}) == remote_bridge.Binding()
    assert remote_bridge.conversation_binding({}).active is False
    assert remote_bridge.conversation_device({"rc_device_id": " a ", "rc_device_name": "Mac"}) == (
        "a",
        "Mac",
    )
    assert remote_bridge.conversation_device({"rc_device_id": None}) == (None, None)


def test_device_names_map_reads_conversation_feed() -> None:
    names = remote_bridge.device_names(
        [{"deviceId": "device-a", "deviceName": "MacBook"}, {"deviceId": "device-b"}]
    )
    assert names == {"device-a": "MacBook"}


# --- состояние команды ----------------------------------------------------


def test_parse_command_reads_flat_state_dto() -> None:
    command = remote_bridge.parse_command(
        {
            "commandId": COMMAND_ID,
            "publicationId": "pub-1",
            "sequence": 1,
            "commandType": "prompt",
            "status": "succeeded",
            "created": False,
            "errorCode": None,
            "expiresAt": NOW + 60,
            "resultSummary": {"crmSessionId": "sess-1"},
        }
    )
    assert command is not None
    assert command.id == COMMAND_ID
    assert command.created is False
    assert remote_bridge.terminal_status(command) == "succeeded"
    assert remote_bridge.crm_session_id(command) == "sess-1"


def test_parse_command_rejects_nested_envelope() -> None:
    # Обёртки {command: {...}, created: ...} на этом контуре нет. Если ответ
    # приехал в ней — вызван не тот маршрут, и «понимать» её молча нельзя.
    assert remote_bridge.parse_command({"command": {"id": COMMAND_ID, "status": "succeeded"}}) is None
    assert remote_bridge.parse_command({"id": COMMAND_ID, "status": "succeeded"}) is None
    assert remote_bridge.parse_command({"created": True}) is None
    assert remote_bridge.parse_command(None) is None


def test_running_status_is_not_terminal() -> None:
    command = remote_bridge.parse_command({"commandId": COMMAND_ID, "status": "running"})
    assert command is not None
    assert remote_bridge.terminal_status(command) is None
    assert remote_bridge.crm_session_id(command) is None


def test_terminal_statuses_match_server_contract() -> None:
    assert remote_bridge.TERMINAL_COMMAND_STATUSES == {
        "succeeded",
        "failed",
        "cancelled",
        "indeterminate",
    }
    # «rejected» серверу не отправить: отказ приезжает как failed + код причины.
    rejected = remote_bridge.parse_command({"commandId": COMMAND_ID, "status": "rejected"})
    assert rejected is not None
    assert remote_bridge.terminal_status(rejected) is None


# --- контракт журнала событий ---------------------------------------------


def test_runner_event_keeps_prefix_and_nested_payload() -> None:
    events = remote_bridge.parse_events(
        {"items": [runner_event("rc.command_status", {"state": "succeeded"})]}
    )
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "rc.command_status"
    assert remote_bridge.is_runner_event(event) is True
    assert remote_bridge.runner_payload(event) == {"state": "succeeded"}
    assert remote_bridge.runner_event_id(event) == "evt-1"
    assert remote_bridge.event_command_state(event) == "succeeded"


def test_runner_event_state_is_not_read_from_top_of_detail() -> None:
    """Выдуманная форма не должна читаться ни при каких условиях.

    Раннер кладёт состояние в ``detail['payload']['state']``; вариант
    ``detail['status']`` у события с префиксом ``rc.`` — это чужой контракт, и
    молча его понимать нельзя, иначе поломка формы останется незамеченной.
    """
    invented = runner_event("rc.command_status", {}, detail={"rcEventId": "e", "status": "succeeded"})
    event = remote_bridge.parse_event(invented)
    assert event is not None
    assert remote_bridge.runner_payload(event) == {}
    assert remote_bridge.event_command_state(event) is None


def test_audit_event_keeps_flat_detail_without_prefix() -> None:
    event = remote_bridge.parse_event(audit_event())
    assert event is not None
    assert event.event_type == remote_bridge.AUDIT_COMMAND_STATUS
    assert remote_bridge.is_runner_event(event) is False
    # У аудита данных в detail['payload'] нет вовсе — читаем плоско.
    assert remote_bridge.runner_payload(event) == {}
    assert remote_bridge.event_command_state(event) == "failed"
    assert remote_bridge.event_error_code(event) == "PRIVACY_DENIED"


def test_audit_event_without_prefix_is_not_confused_with_runner_type() -> None:
    # «command_status» и «rc.command_status» — РАЗНЫЕ типы с разной формой detail.
    assert remote_bridge.AUDIT_COMMAND_STATUS not in remote_bridge.RUNNER_EVENT_TYPES
    assert remote_bridge.EVENT_COMMAND_STATUS in remote_bridge.RUNNER_EVENT_TYPES
    nested_audit = remote_bridge.parse_event(
        audit_event(detail={"payload": {"state": "succeeded"}})
    )
    assert nested_audit is not None
    # Аудит вложенного payload не имеет: подсунутая форма статуса не даёт.
    assert remote_bridge.event_command_state(nested_audit) is None


def test_events_of_foreign_command_are_not_mine() -> None:
    mine = remote_bridge.parse_event(runner_event("rc.progress", {"text": "мой шаг"}))
    foreign = remote_bridge.parse_event(
        runner_event("rc.progress", {"text": "чужой шаг"}, commandId="cmd-2")
    )
    publication_row = remote_bridge.parse_event(
        {"id": "1", "eventType": "publication_created", "detail": {"privacyMode": "crm"}}
    )
    assert mine is not None and foreign is not None and publication_row is not None
    assert remote_bridge.belongs_to(mine, COMMAND_ID) is True
    assert remote_bridge.belongs_to(foreign, COMMAND_ID) is False
    # Строка публикации приходит без commandId и в логику turn-а не входит.
    assert publication_row.command_id is None
    assert remote_bridge.belongs_to(publication_row, COMMAND_ID) is False


def test_event_notes_and_steps_come_from_nested_payload() -> None:
    progress = remote_bridge.parse_event(runner_event("rc.progress", {"text": " почти готово "}))
    tool = remote_bridge.parse_event(
        runner_event("rc.tool_call", {"tool": "Edit", "status": "ok", "path": "core/db.py"})
    )
    approval = remote_bridge.parse_event(
        runner_event("rc.approval_required", {"tool": "Bash", "reason": "rm"})
    )
    diff = remote_bridge.parse_event(
        runner_event("rc.diff_summary", {"filesChanged": 2, "insertions": 9, "deletions": 1})
    )
    assert progress is not None and tool is not None
    assert approval is not None and diff is not None
    assert remote_bridge.event_note(progress) == "почти готово"
    assert remote_bridge.event_step(tool) == {"status": "ok", "desc": "Edit · core/db.py"}
    note = remote_bridge.event_note(approval)
    assert note is not None and "только за компьютером" in note
    assert remote_bridge.event_note(diff) == "📄 Правки: файлов 2, +9/−1"
    assert remote_bridge.event_step(progress) is None


def test_event_progress_text_is_capped() -> None:
    long_text = "я" * (remote_bridge.MAX_EVENT_TEXT + 500)
    event = remote_bridge.parse_event(runner_event("rc.progress", {"text": long_text}))
    assert event is not None
    note = remote_bridge.event_note(event)
    assert note is not None and len(note) == remote_bridge.MAX_EVENT_TEXT


def test_events_cursor_uses_server_value_and_stops_on_empty_page() -> None:
    payload = {"items": [audit_event(id="7")], "nextCursor": "7"}
    assert remote_bridge.events_cursor(payload) == "7"
    # Пустая страница курсор НЕ двигает.
    assert remote_bridge.events_cursor({"items": [], "nextCursor": None}) is None
    # bigint приходит строкой; число тоже принимаем, но отдаём строкой.
    assert remote_bridge.events_cursor({"items": [], "nextCursor": 9007199254740993}) == (
        "9007199254740993"
    )


def test_parse_event_drops_rows_without_id_or_type() -> None:
    assert remote_bridge.parse_event({"eventType": "command_status"}) is None
    assert remote_bridge.parse_event({"id": "1"}) is None
    assert remote_bridge.parse_events({"items": [{"id": "1"}, audit_event()]}) != []


# --- адреса маршрутов ------------------------------------------------------


def test_rc_endpoint_points_at_canonical_bot_routes(monkeypatch: Any) -> None:
    """Адреса /rc обязаны совпадать с реально существующими маршрутами сервера.

    Контур бота — только ``hereassistant-sync/rc/*``. Маршрутов
    ``hereassistant-sync/remote-publications*`` не существует вовсе, а
    ``cli-agent/remote-publications`` закрыт браузерным JWT: ошибка в префиксе
    даёт 404 на каждом вызове и выглядит как «публикация не найдена».
    """
    from core import config, herecrm_client

    monkeypatch.setattr(config, "HERECRM_SYNC_URL", "https://api.example.com/api/v1")
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_token")

    base = "https://api.example.com/api/v1/hereassistant-sync/rc"
    assert herecrm_client.rc_configured() is True
    assert herecrm_client.rc_endpoint("publications") == f"{base}/publications"
    assert herecrm_client.rc_endpoint("publications/abc/commands") == (
        f"{base}/publications/abc/commands"
    )
    assert herecrm_client.rc_endpoint("publications/abc/commands/xyz") == (
        f"{base}/publications/abc/commands/xyz"
    )
    assert herecrm_client.rc_endpoint("publications/abc/events") == f"{base}/publications/abc/events"
    for route in ("publications", "publications/abc/events"):
        url = herecrm_client.rc_endpoint(route)
        assert "remote-publications" not in url
        assert "cli-agent" not in url
        assert "/hereassistant-sync/rc/" in url


def test_rc_uses_the_same_scoped_token_as_history_sync(monkeypatch: Any) -> None:
    """Отдельных переменных у канала нет: права решают scopes токена."""
    from core import config, herecrm_client

    monkeypatch.setattr(config, "HERECRM_SYNC_URL", "https://api.example.com/api/v1")
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_token")
    assert herecrm_client.rc_configured() is herecrm_client.configured() is True
    assert not hasattr(config, "RC_CONTROL_PLANE_URL")
    assert not hasattr(config, "RC_CONTROL_PLANE_TOKEN")


def test_rc_endpoint_is_disabled_without_crm_settings(monkeypatch: Any) -> None:
    from core import config, herecrm_client

    monkeypatch.setattr(config, "HERECRM_SYNC_URL", "")
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "")

    assert herecrm_client.rc_configured() is False
    try:
        herecrm_client.rc_endpoint("publications")
    except herecrm_client.HereCrmClientError as error:
        assert error.code == "rc_not_configured"
    else:  # pragma: no cover - защита от молчаливого включения режима
        raise AssertionError("rc_endpoint обязан отказать без настроек HereCRM")


def test_rc_endpoint_requires_absolute_https_url(monkeypatch: Any) -> None:
    from core import config, herecrm_client

    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_token")
    for url in ("http://api.example.com", "api.example.com"):
        monkeypatch.setattr(config, "HERECRM_SYNC_URL", url)
        try:
            herecrm_client.rc_endpoint("publications")
        except herecrm_client.HereCrmClientError as error:
            assert error.code == "rc_not_configured"
        else:  # pragma: no cover
            raise AssertionError("не-https база обязана отклоняться")
