"""Мост «сообщение в чате Telegram → команда в опубликованную сессию /rc».

Чистый слой без сети и без aiogram: разбор ответов control-plane, выбор живой
публикации нужной сессии, классификация отказов и готовые тексты для чата.

Решения о приватности здесь НЕ принимаются. Единственный источник —
``core/project_config`` на самом устройстве; сюда приходит только снимок
``capabilities``, который устройство опубликовало само. Если снимок ничего не
разрешает, мост отказывает: молчаливого фолбэка на серверную сессию нет.

Две формы события в одной таблице аудита — главная тонкость этого файла:

* событие РАННЕРА имеет ``eventType`` с префиксом ``rc.`` (``rc.command_status``,
  ``rc.progress``, ...), полезные данные лежат в ``detail['payload']``, ключ
  дедупликации — ``detail['rcEventId']``. Состояние команды в нём называется
  ``state``, а не ``status``;
* строка АУДИТА СЕРВЕРА имеет ``eventType`` без префикса (``command_status``,
  ``command_claimed``, ...), а её поля лежат прямо в ``detail`` (``status``,
  ``errorCode``).

Обе приезжают одним ответом ``GET rc/publications/:id/events``, и различать их
можно ТОЛЬКО по ``eventType``. Никакое событие не является завершением turn-а:
источник истины по статусу — строка команды (``GET .../commands/:commandId``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .remote_control.config import OFFLINE_AFTER_SEC

# Состояния публикации, из которых команда уже никогда не будет исполнена.
TERMINAL_PUBLICATION_STATES = frozenset({"closed", "expired", "revoked", "failed"})

# Терминальные статусы команды на стороне control-plane. Список закрыт
# контрактом сервера (RunnerCommandResultDto): раннер физически не может
# записать сюда ничего другого, поэтому «rejected» устройства приезжает как
# failed с кодом ошибки — см. chat_remote_control._report_command_result.
TERMINAL_COMMAND_STATUSES = frozenset({"succeeded", "failed", "cancelled", "indeterminate"})

# --- контракт журнала событий ----------------------------------------------
# Типы событий раннера. Префикс `rc.` сохраняется сервером ДОСЛОВНО.
RUNNER_EVENT_PREFIX = "rc."
EVENT_PROGRESS = "rc.progress"
EVENT_TOOL_CALL = "rc.tool_call"
EVENT_APPROVAL_REQUIRED = "rc.approval_required"
EVENT_DIFF_SUMMARY = "rc.diff_summary"
EVENT_COMMAND_STATUS = "rc.command_status"
RUNNER_EVENT_TYPES = frozenset(
    {
        EVENT_PROGRESS,
        EVENT_TOOL_CALL,
        EVENT_APPROVAL_REQUIRED,
        EVENT_DIFF_SUMMARY,
        EVENT_COMMAND_STATUS,
    }
)

# Строки аудита, которые пишет сам сервер. Форма detail у них ДРУГАЯ.
AUDIT_COMMAND_STATUS = "command_status"
AUDIT_EVENT_TYPES = frozenset(
    {
        "publication_created",
        "publication_closed",
        "command_created",
        "command_claimed",
        AUDIT_COMMAND_STATUS,
    }
)

# Раннер обрезает текст события до 800 символов (events.MAX_TEXT_CHARS); столько
# же читаем и мы, чтобы одно событие не распухло в ленте прогресса.
MAX_EVENT_TEXT = 800

# Коды отказа моста. Совпадают по духу с RC_ERROR_TEXT во фронте WebApp.
NO_DEVICE = "no_device"
NO_PUBLICATION = "no_publication"
SESSION_MOVED = "session_moved"
DEVICE_OFFLINE = "device_offline"
PUBLICATION_CLOSED = "publication_closed"
PROMPT_DENIED = "prompt_denied"
STOP_DENIED = "stop_denied"
NOT_CONFIGURED = "not_configured"
ATTACHMENTS_UNSUPPORTED = "attachments_unsupported"
COMMAND_EXPIRED = "command_expired"

# Отказы, после которых привязка треда к сессии заведомо мертва.
UNBIND_REFUSALS = frozenset(
    {
        PUBLICATION_CLOSED,
        SESSION_MOVED,
        "rc_forbidden",
        "rc_not_found",
        "rc_publication_closed",
    }
)

REFUSAL_TEXT: dict[str, str] = {
    NO_DEVICE: ("Удалённый режим не включён для этого треда. Выполни /rc и выбери сессию."),
    NO_PUBLICATION: (
        "Нет опубликованных сессий этого устройства. На компьютере в чате "
        "HereAssistant выполни /rc, чтобы опубликовать текущую сессию."
    ),
    SESSION_MOVED: (
        "Публикация этой сессии закрыта или сменилась — выбери устройство и "
        "сессию заново через /rc. Отправлять запрос в другую публикацию того же "
        "компьютера нельзя: это другой проект и другая политика приватности."
    ),
    DEVICE_OFFLINE: (
        "Устройство не выходит на связь (heartbeat старше "
        f"{int(OFFLINE_AFTER_SEC)} с). Запрос НЕ отправлен: команду некому забрать, "
        "а отменить её потом нельзя. Проверь, что чат HereAssistant на компьютере "
        "запущен, и повтори."
    ),
    PUBLICATION_CLOSED: (
        "Публикация закрыта или просрочена. Опубликуй сессию заново на компьютере "
        "(/rc), затем выбери её здесь через /rc."
    ),
    PROMPT_DENIED: (
        "Устройство не принимает удалённые промпты: проект приватный либо в его "
        ".hereassistant/project.yml выключен send_prompts. Это политика проекта, "
        "и обойти её из Telegram нельзя."
    ),
    STOP_DENIED: (
        "Устройство не разрешает удалённую остановку. Прерви запуск в терминале "
        "на самом компьютере."
    ),
    NOT_CONFIGURED: (
        "Канал HereCRM не настроен (нет HERECRM_SYNC_URL/HERECRM_SYNC_TOKEN) — "
        "режим /rc недоступен."
    ),
    ATTACHMENTS_UNSUPPORTED: (
        "В удалённом режиме вложения пока не поддерживаются: файл не уедет на "
        "устройство. Отправь текстом или переключись обратно командой /rc off."
    ),
    COMMAND_EXPIRED: (
        "Команда просрочена — устройство её так и не забрало. Проверь чат "
        "HereAssistant на компьютере."
    ),
    "rc_forbidden": (
        "Сервер отказал в доступе к публикации: устройство отозвано либо у "
        "токена нет scope rc:read/rc:command. Привязка снята — перевыпусти токен "
        "HERECRM_SYNC_TOKEN с нужными scopes."
    ),
    "rc_not_found": "Публикация не найдена на сервере. Привязка снята.",
    "rc_publication_closed": (
        "Сервер отклонил команду: публикация закрыта, просрочена или устройство "
        "не на связи. Привязка снята."
    ),
    "rc_unavailable": (
        "Сервер недоступен. Статус команды неизвестен — проверь ход работы "
        "на самом устройстве, прежде чем повторять запрос."
    ),
    "crm_not_configured": ("Канал HereCRM не настроен — удалённый режим недоступен."),
    "rc_not_configured": (
        "Канал HereCRM не настроен (нет HERECRM_SYNC_URL/HERECRM_SYNC_TOKEN) — "
        "режим /rc недоступен."
    ),
    "crm_token_needs_read_scope": (
        "У токена HereCRM нет прав на чтение — текст ответа из сессии CRM забрать "
        "нечем. Перевыпусти токен со scope sessions:read."
    ),
}

_UNKNOWN_REFUSAL = (
    "Удалённый запуск не состоялся. Проверь состояние публикации командой /rc status."
)

# Расшифровка кодов причины. Часть кодов ставит устройство вместе с терминальным
# статусом, часть — сервер в теле HTTP-ошибки; человеку нужна разница между
# «проект запретил промпты» и «подтверждение можно дать только за компьютером».
ERROR_CODE_TEXT: dict[str, str] = {
    "PRIVACY_DENIED": "проект на устройстве запрещает удалённые промпты",
    "APPROVAL_LOCAL_ONLY": (
        "подтверждение инструмента можно дать только за компьютером — из Telegram "
        "подтверждений нет"
    ),
    "RUN_FAILED": "запуск агента на устройстве завершился ошибкой",
    "GIT_ACTION_FAILED": "git-действие на устройстве не удалось",
    "PAYLOAD_MISMATCH": "устройство получило команду с другим содержимым и не стало её исполнять",
    "UNKNOWN_COMMAND_TYPE": "устройство не знает такого типа команды",
    "RESULT_UNKNOWN": "результат неизвестен: отмена, перезапуск или истёкшая аренда команды",
    "DEVICE_OFFLINE": "устройство не выходило на связь, сервер команду не принял",
    "PUBLICATION_CLOSED": "публикация закрыта или просрочена",
    "CAPABILITY_UNAVAILABLE": "политика проекта не разрешает это действие",
    "IDEMPOTENCY_KEY_REQUIRED": "сервер не принял команду без ключа идемпотентности",
    "IDEMPOTENCY_KEY_INVALID": "сервер отклонил ключ идемпотентности",
}


def refusal_text(code: str) -> str:
    """Человеческий текст отказа; неизвестный код не молчит."""
    return REFUSAL_TEXT.get(code, _UNKNOWN_REFUSAL)


def error_code_text(code: str | None) -> str | None:
    """Расшифровка кода причины; незнакомый код показываем как есть, не глотаем."""
    if not code:
        return None
    return ERROR_CODE_TEXT.get(code)


def should_unbind(code: str) -> bool:
    """Нужно ли снять привязку треда после этого отказа."""
    return code in UNBIND_REFUSALS


@dataclass(frozen=True, slots=True)
class Publication:
    """Проекция публикации, которую отдаёт ``GET rc/publications``.

    Имя, платформа и состояние устройства приходят в ЭТОМ ЖЕ ответе (сервер
    делает LEFT JOIN на таблицу устройств), поэтому лента диалогов CRM для имён
    больше не нужна — у HereAssistant-сессий там всё равно нет ``deviceId``.
    """

    id: str
    public_id: str
    state: str
    device_id: str
    device_name: str
    device_platform: str
    device_status: str
    conversation_id: str | None
    privacy_mode: str
    capabilities: Mapping[str, Any]
    published_at: float
    last_heartbeat_at: float | None
    expires_at: float | None
    close_reason: str
    online: bool | None
    heartbeat_age_sec: float | None


@dataclass(frozen=True, slots=True)
class RemoteCommand:
    """Состояние команды по плоскому ответу сервера (``created`` — тоже поле)."""

    id: str
    status: str
    created: bool
    error_code: str | None
    expires_at: float | None
    result_summary: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RemoteEvent:
    """Строка журнала публикации: либо событие раннера, либо аудит сервера."""

    id: str
    event_type: str
    outcome: str
    command_id: str | None
    device_id: str | None
    created_at: float | None
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Selection:
    """Результат выбора цели: либо публикация, либо код отказа."""

    publication: Publication | None
    refusal: str | None


@dataclass(frozen=True, slots=True)
class Binding:
    """Привязка треда Telegram: устройство И конкретная сессия его публикации."""

    device_id: str | None = None
    device_name: str | None = None
    publication_id: str | None = None
    conversation_id: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.device_id)

    @property
    def label(self) -> str:
        return self.device_name or self.device_id or "устройство"


def parse_timestamp(value: Any) -> float | None:
    """Читает ISO-8601 или unix (сек/мс). Непонятное значение — None, не ноль."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 1e11 else number
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def list_items(payload: Any) -> list[Any]:
    """Терпимо разбирает и голый список, и обёртку {items|publications|events}."""
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, Mapping):
        for key in ("items", "publications", "events", "data"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return list(nested)
    return []


def _text(raw: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _number(raw: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _flag(raw: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, bool):
            return value
    return None


def parse_publication(raw: Any, names: Mapping[str, str] | None = None) -> Publication | None:
    """Строит проекцию; строка без id устройства бесполезна и отбрасывается.

    ``names`` — фолбэк-карта «id устройства → имя» из ленты диалогов CRM, нужный
    только старым ответам сервера без ``deviceName``. Придумывать имя нельзя:
    без обоих источников в интерфейсе честно останется «устройство».
    """
    if not isinstance(raw, Mapping):
        return None
    publication_id = _text(raw, "id")
    device_id = _text(raw, "deviceId", "device_id")
    if not publication_id or not device_id:
        return None
    capabilities = raw.get("capabilities")
    name = _text(raw, "deviceName", "device_name") or (names or {}).get(device_id) or "устройство"
    return Publication(
        id=publication_id,
        public_id=_text(raw, "publicId", "public_id"),
        state=_text(raw, "state", default="unknown"),
        device_id=device_id,
        device_name=name,
        device_platform=_text(raw, "devicePlatform", "device_platform"),
        device_status=_text(raw, "deviceStatus", "device_status"),
        conversation_id=_text(raw, "conversationId", "conversation_id") or None,
        privacy_mode=_text(raw, "privacyMode", "privacy_mode"),
        capabilities=capabilities if isinstance(capabilities, Mapping) else {},
        published_at=parse_timestamp(raw.get("publishedAt") or raw.get("published_at")) or 0.0,
        last_heartbeat_at=parse_timestamp(
            raw.get("lastHeartbeatAt") or raw.get("last_heartbeat_at")
        ),
        expires_at=parse_timestamp(raw.get("expiresAt") or raw.get("expires_at")),
        close_reason=_text(raw, "closeReason", "close_reason"),
        online=_flag(raw, "online"),
        heartbeat_age_sec=_number(raw, "heartbeatAgeSec", "heartbeat_age_sec"),
    )


def parse_publications(payload: Any, names: Mapping[str, str] | None = None) -> list[Publication]:
    parsed = (parse_publication(raw, names) for raw in list_items(payload))
    return [publication for publication in parsed if publication is not None]


def device_names(payload: Any) -> dict[str, str]:
    """Фолбэк-карта «устройство → имя» из ленты диалогов CRM.

    Практически всегда пуста: ``cli_agent_conversations.device_id`` у сессий
    HereAssistant не заполняется. Основной источник имени — сама публикация.
    """
    names: dict[str, str] = {}
    for item in list_items(payload):
        if not isinstance(item, Mapping):
            continue
        device_id = _text(item, "deviceId", "device_id")
        name = _text(item, "deviceName", "device_name")
        if device_id and name and device_id not in names:
            names[device_id] = name
    return names


def parse_command(payload: Any) -> RemoteCommand | None:
    """Разбирает ПЛОСКИЙ ответ о состоянии команды.

    Форма одна и та же у постановки (201) и у чтения статуса: поля лежат на
    верхнем уровне, идентификатор называется ``commandId``. Обёртки
    ``{command: {...}, created: ...}`` на этом контуре нет — если ответ приехал
    в ней, значит вызван не тот маршрут, и такое молча «понимать» нельзя.
    """
    if not isinstance(payload, Mapping):
        return None
    command_id = _text(payload, "commandId", "command_id")
    if not command_id:
        return None
    created = payload.get("created")
    summary = payload.get("resultSummary") or payload.get("result_summary")
    return RemoteCommand(
        id=command_id,
        status=_text(payload, "status", default="pending"),
        created=created is not False,
        error_code=_text(payload, "errorCode", "error_code") or None,
        expires_at=parse_timestamp(payload.get("expiresAt") or payload.get("expires_at")),
        result_summary=summary if isinstance(summary, Mapping) else {},
    )


def terminal_status(command: RemoteCommand) -> str | None:
    """Терминальный статус команды либо None, пока она ещё в работе."""
    return command.status if command.status in TERMINAL_COMMAND_STATUSES else None


def crm_session_id(command: RemoteCommand) -> str | None:
    """UUID сессии CRM из redacted-итога команды; его кладёт само устройство."""
    value = command.result_summary.get("crmSessionId")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# --- журнал событий ---------------------------------------------------------


def parse_event(raw: Any) -> RemoteEvent | None:
    """Строка журнала. Без id и типа она бесполезна: курсор двигать нечем."""
    if not isinstance(raw, Mapping):
        return None
    event_id = _text(raw, "id")
    event_type = _text(raw, "eventType", "event_type")
    if not event_id or not event_type:
        return None
    detail = raw.get("detail")
    return RemoteEvent(
        id=event_id,
        event_type=event_type,
        outcome=_text(raw, "outcome"),
        command_id=_text(raw, "commandId", "command_id") or None,
        device_id=_text(raw, "deviceId", "device_id") or None,
        created_at=parse_timestamp(raw.get("createdAt") or raw.get("created_at")),
        detail=detail if isinstance(detail, Mapping) else {},
    )


def parse_events(payload: Any) -> list[RemoteEvent]:
    parsed = (parse_event(raw) for raw in list_items(payload))
    return [event for event in parsed if event is not None]


def events_cursor(payload: Any) -> str | None:
    """``nextCursor`` строкой. Пустая страница курсор НЕ двигает."""
    if isinstance(payload, Mapping):
        cursor = payload.get("nextCursor") or payload.get("next_cursor")
        if isinstance(cursor, str) and cursor.strip():
            return cursor.strip()
        if isinstance(cursor, int) and not isinstance(cursor, bool):
            return str(cursor)
    events = parse_events(payload)
    return events[-1].id if events else None


def is_runner_event(event: RemoteEvent) -> bool:
    """Событие устройства ⇔ префикс ``rc.`` в типе. Аудит сервера его не имеет."""
    return event.event_type.startswith(RUNNER_EVENT_PREFIX)


def runner_payload(event: RemoteEvent) -> Mapping[str, Any]:
    """Полезные данные события раннера лежат ВЛОЖЕННО, в ``detail['payload']``."""
    if not is_runner_event(event):
        return {}
    payload = event.detail.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def runner_event_id(event: RemoteEvent) -> str | None:
    """Ключ дедупликации события раннера — ``detail['rcEventId']``."""
    value = event.detail.get("rcEventId")
    return value.strip() if isinstance(value, str) and value.strip() else None


def belongs_to(event: RemoteEvent, command_id: str) -> bool:
    """Своя команда ⇔ совпал верхнеуровневый ``commandId``, а не что-то в detail.

    События публикации (создана/закрыта) приходят с ``commandId = null`` и в
    логику turn-а не входят вовсе.
    """
    return bool(event.command_id) and event.command_id == command_id


def event_command_state(event: RemoteEvent) -> str | None:
    """Подсказка о состоянии команды из события. Завершением turn-а НЕ является.

    Два разных места, и путать их нельзя: у события раннера состояние лежит в
    ``detail['payload']['state']``, у строки аудита сервера — в
    ``detail['status']``.
    """
    if event.event_type == EVENT_COMMAND_STATUS:
        state = runner_payload(event).get("state")
        return state.strip() if isinstance(state, str) and state.strip() else None
    if event.event_type == AUDIT_COMMAND_STATUS:
        status = event.detail.get("status")
        return status.strip() if isinstance(status, str) and status.strip() else None
    return None


def event_error_code(event: RemoteEvent) -> str | None:
    """Код причины пишет только аудит сервера — у раннера его в событии нет."""
    if event.event_type not in AUDIT_EVENT_TYPES:
        return None
    code = event.detail.get("errorCode")
    return code.strip() if isinstance(code, str) and code.strip() else None


def event_step(event: RemoteEvent) -> dict[str, str] | None:
    """Шаг для ленты прогресса из ``rc.tool_call``; статус — иконка рендера."""
    if event.event_type != EVENT_TOOL_CALL:
        return None
    payload = runner_payload(event)
    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return None
    status = payload.get("status")
    path = payload.get("path")
    description = tool.strip()
    if isinstance(path, str) and path.strip():
        description = f"{description} · {path.strip()[:120]}"
    known = {"ok": "ok", "success": "ok", "done": "ok", "err": "err", "error": "err", "failed": "err"}
    return {
        "status": known.get(str(status).strip().lower(), "run"),
        "desc": description,
    }


def event_note(event: RemoteEvent) -> str | None:
    """Человеческая строка события для ленты прогресса. Текст не додумываем."""
    payload = runner_payload(event)
    if event.event_type == EVENT_PROGRESS:
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:MAX_EVENT_TEXT]
        return None
    if event.event_type == EVENT_APPROVAL_REQUIRED:
        tool = str(payload.get("tool") or "инструмент").strip()[:120]
        reason = payload.get("reason")
        tail = f" ({str(reason).strip()[:160]})" if isinstance(reason, str) and reason.strip() else ""
        return (
            f"⚠️ Устройство просит подтверждение: {tool}{tail}. Подтвердить можно "
            "только за компьютером — из Telegram подтверждений нет."
        )
    if event.event_type == EVENT_DIFF_SUMMARY:
        files = payload.get("filesChanged")
        insertions = payload.get("insertions")
        deletions = payload.get("deletions")
        if isinstance(files, int) and not isinstance(files, bool):
            plus = insertions if isinstance(insertions, int) else 0
            minus = deletions if isinstance(deletions, int) else 0
            return f"📄 Правки: файлов {files}, +{plus}/−{minus}"
        return None
    return None


# --- состояние публикации ---------------------------------------------------


def heartbeat_age(publication: Publication, now: float) -> float | None:
    if publication.last_heartbeat_at is not None:
        return max(0.0, now - publication.last_heartbeat_at)
    # Сервер считает свежесть сам и отдаёт её отдельным полем — если метки
    # времени в ответе нет, честнее взять его число, чем считать «связи не было».
    return publication.heartbeat_age_sec


def is_expired(publication: Publication, now: float) -> bool:
    return publication.expires_at is not None and publication.expires_at <= now


def is_closed(publication: Publication, now: float) -> bool:
    return publication.state in TERMINAL_PUBLICATION_STATES or is_expired(publication, now)


def is_online(publication: Publication, now: float) -> bool:
    """Живой heartbeat — единственный признак присутствия, который не врёт.

    Серверное ``online: false`` уважаем безусловно: оно считалось по его часам и
    по той же константе OFFLINE_AFTER_SEC, а расхождение часов должно приводить
    к отказу, а не к отправке команды в пустоту.
    """
    if is_closed(publication, now):
        return False
    if publication.online is False:
        return False
    age = heartbeat_age(publication, now)
    return age is not None and age <= OFFLINE_AFTER_SEC


def allows(publication: Publication, capability: str) -> bool:
    """Разрешение считается данным только явным True — default deny."""
    return publication.capabilities.get(capability) is True


def is_live(publication: Publication, now: float) -> bool:
    """Живая публикация: не закрыта, на связи и принимает удалённые промпты."""
    return is_online(publication, now) and allows(publication, "remotePrompt")


def live_publications(publications: Iterable[Publication], now: float) -> list[Publication]:
    """Живые публикации: по одной на сессию CRM, свежие сверху.

    Дедупликация именно по сессии, а не по устройству: один компьютер держит
    несколько проектов одновременно, и склеивать их в одну кнопку — значит
    однажды отправить запрос не туда.
    """
    newest: dict[str, Publication] = {}
    for publication in publications:
        if not is_live(publication, now):
            continue
        key = (
            f"conv:{publication.conversation_id}"
            if publication.conversation_id
            else f"pub:{publication.id}"
        )
        current = newest.get(key)
        if current is None or publication.published_at > current.published_at:
            newest[key] = publication
    return sorted(newest.values(), key=lambda item: item.published_at, reverse=True)


def find_publication(
    publications: Iterable[Publication], publication_id: str
) -> Publication | None:
    """Публикация по её id — так пикер сохраняет выбор конкретной сессии."""
    for publication in publications:
        if publication.id == publication_id:
            return publication
    return None


def select_target(
    publications: Sequence[Publication],
    device_id: str | None,
    *,
    now: float,
    capability: str = "remotePrompt",
    conversation_id: str | None = None,
) -> Selection:
    """Выбирает публикацию для команды либо объясняет, почему её нет.

    Цель — СЕССИЯ, а не компьютер. Когда тред помнит ``conversation_id``, только
    он и решает: совпадение по устройству цели не даёт, а молчаливый переход на
    другую публикацию того же компьютера запрещён (это чужой проект).
    """
    if not device_id and not conversation_id:
        return Selection(None, NO_DEVICE)

    if conversation_id:
        owned = [
            item
            for item in publications
            if item.conversation_id == conversation_id
            # Сессия не должна «переезжать» на другую машину незаметно для человека.
            and (not device_id or item.device_id == device_id)
        ]
        missing = SESSION_MOVED
    else:
        owned = [item for item in publications if item.device_id == device_id]
        missing = NO_PUBLICATION
    if not owned:
        return Selection(None, missing)

    alive = [item for item in owned if not is_closed(item, now)]
    if not alive:
        return Selection(None, SESSION_MOVED if conversation_id else PUBLICATION_CLOSED)
    online = [item for item in alive if is_online(item, now)]
    if not online:
        return Selection(None, DEVICE_OFFLINE)
    if not conversation_id and len(online) > 1:
        # Привязка помнит только устройство, а живых сессий у него несколько:
        # «самая свежая» однажды окажется чужим проектом. Просим выбрать заново.
        return Selection(None, SESSION_MOVED)

    target = max(online, key=lambda item: item.published_at)
    if not allows(target, capability):
        return Selection(None, STOP_DENIED if capability == "stop" else PROMPT_DENIED)
    return Selection(target, None)


def idempotency_key(kind: str, chat_id: int, thread_id: int, message_id: int) -> str:
    """Детерминированный ключ: ретрай сети не создаёт второй запуск агента."""
    prefix = "ha-tg" if kind == "prompt" else f"ha-tg-{kind}"
    return f"{prefix}:{int(chat_id)}:{int(thread_id)}:{int(message_id)}"


def _column(conversation: Any, name: str) -> str | None:
    """Читает колонку строки БД; старая схема без колонки — не ошибка."""
    try:
        value = conversation[name]
    except (IndexError, KeyError, TypeError):
        return None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def conversation_binding(conversation: Any) -> Binding:
    """Полная привязка треда: устройство, публикация и сессия CRM."""
    return Binding(
        device_id=_column(conversation, "rc_device_id"),
        device_name=_column(conversation, "rc_device_name"),
        publication_id=_column(conversation, "rc_publication_id"),
        conversation_id=_column(conversation, "rc_conversation_id"),
    )


def conversation_device(conversation: Any) -> tuple[str | None, str | None]:
    """Короткая форма для развилки обработчика сообщений: устройство и имя."""
    binding = conversation_binding(conversation)
    return binding.device_id, binding.device_name


def format_device_line(publication: Publication, now: float) -> str:
    """Одна строка про цель: имя, состояние публикации, свежесть связи, сессия."""
    parts = [publication.device_name, publication.state]
    age = heartbeat_age(publication, now)
    parts.append("связи не было" if age is None else f"связь {int(age)} с назад")
    if publication.privacy_mode == "crm" and publication.conversation_id:
        # Имени проекта в ответе нет, и придумывать его нельзя: показываем
        # короткий признак сессии, чтобы две публикации одной машины различались.
        parts.append(f"сессия {publication.conversation_id[:8]}")
    elif publication.privacy_mode and publication.privacy_mode != "crm":
        parts.append(f"приватность {publication.privacy_mode}")
    return " · ".join(parts)


def capabilities_line(publication: Publication) -> str:
    """Что устройство разрешило: показываем как есть, ничего не додумывая."""
    labels = (
        ("remotePrompt", "промпты"),
        ("stop", "остановка"),
        ("gitCommit", "git commit"),
        ("gitPush", "git push"),
        ("toolEvents", "события инструментов"),
    )
    granted = [title for key, title in labels if allows(publication, key)]
    return ", ".join(granted) if granted else "только присутствие"
