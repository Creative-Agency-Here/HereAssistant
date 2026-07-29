"""Удалённый turn: сообщение чата уезжает командой в сессию /rc на устройстве.

Инварианты этого модуля:

* молчаливого фолбэка на серверную сессию НЕТ. Недоступное устройство — это
  видимый отказ, а не тихий запуск агента не там, где ждёт человек;
* цель turn-а — конкретная ПУБЛИКАЦИЯ (сессия CRM), а не «какой-то компьютер»:
  иначе старый тред однажды уедет в другой проект той же машины;
* офлайн-устройство отсекается ДО единственного POST: отмены команды в
  контракте control-plane нет, и промпт часовой давности запустился бы сам;
* статус читается ОТДЕЛЬНЫМ маршрутом состояния команды. Повторный POST тем же
  ключом идемпотентности как способ опроса запрещён: это запись, а не чтение;
* ждём не дольше срока жизни самой команды. Бесконечный опрос держал бы признак
  занятости процесса и мешал остальным запросам бота;
* после успешной постановки каждый следующий шаг живёт в собственном ``try``.
  Иначе сбой косметики покажется провалом отправки, человек повторит запрос —
  и на устройстве стартует второй агент;
* ключ идемпотентности детерминирован от исходного сообщения Telegram, поэтому
  сетевой ретрай не создаёт второй запуск;
* подтверждения инструментов из Telegram не выдаются ни в каком виде: об этом
  честно пишется в чат.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from core import config, events, herecrm_client, remote_bridge
from core.herecrm_client import HereCrmClientError
from utils.markdown import html_escape

from . import repo
from .message_final import prepare_final_payload
from .message_final_delivery import FinalDelivery, FinalDeliveryRequest
from .message_live import LiveSessionPolicy, MessageLiveSession
from .message_queue import QueuedRun
from .message_state import ThreadKey, runtime

log = logging.getLogger("bridge.rc")

# Опрос состояния команды: GET .../commands/:commandId. Живого потока ответа нет
# и имитировать его нельзя — события уезжают с устройства пачками по heartbeat.
POLL_INTERVAL_SEC = 5.0
POLL_MAX_INTERVAL_SEC = 20.0
CLAIM_WARN_AFTER_SEC = 60.0
# Потолок ожидания. Основной срок — expiresAt самой команды; эта константа
# работает как страховка (сервер не сообщил срок либо сообщил абсурдный) и как
# граница занятости процесса: висеть час в опросе нельзя.
MAX_TURN_SEC = 900.0
# Запас после истечения команды, чтобы успеть прочитать её финальный статус.
POLL_GRACE_SEC = 10.0
# Ретраи ТОЛЬКО тем же ключом идемпотентности и только при сетевой неудаче.
CREATE_RETRIES = 2
# Текст ответа приезжает в CRM отдельным outbox устройства — ждём его недолго.
FEED_ATTEMPTS = 8
FEED_INTERVAL_SEC = 3.0
# Сколько шагов из журнала держим в ленте прогресса.
PROGRESS_STEPS_LIMIT = 30

_PROGRESS_POLICY = LiveSessionPolicy(
    progress_enabled=True,
    progress_min_interval=1.5,
    progress_max_interval=15.0,
    progress_backoff_factor=1.6,
    progress_reset_successes=5,
    progress_quiet_after=600.0,
    progress_quiet_interval=30.0,
    progress_chain_limit=15,
    progress_max_chars=3500,
    progress_heartbeat_interval=1.0,
    progress_heartbeat_idle=30.0,
    typing_interval=4.0,
    draft_min_interval=1.0,
)

# Итоги turn-а. Первые четыре — статусы команды на сервере, последние два наши:
# `expired` — сервер отказался вести команду дальше, `timeout` — упёрлись в свой
# потолок ожидания, и работа на устройстве могла продолжиться.
_STATUS_TEXT = {
    "succeeded": "✅ Готово",
    "failed": "❌ Устройство сообщило об ошибке",
    "cancelled": "⏹ Запрос отменён на устройстве",
    "indeterminate": "❔ Устройство не смогло подтвердить результат",
    "expired": "⌛ Команда просрочена — устройство её так и не забрало",
    "timeout": "⏱ Жду слишком долго — работа на устройстве могла продолжиться",
}

# Человеческие подписи статусов команды для ленты прогресса.
_PROGRESS_TEXT = {
    "pending": "ждёт, пока устройство заберёт запрос",
    "claimed": "запрос принят устройством",
    "running": "агент работает на устройстве",
}

# Живые задачи удалённых turn-ов: держим ссылку, иначе сборщик мусора съест их.
_active: dict[ThreadKey, set[asyncio.Task[None]]] = {}


@dataclass(frozen=True, slots=True)
class RemoteOutcome:
    """Итог удалённого запуска в терминах control-plane."""

    status: str
    error_code: str | None = None
    crm_session_id: str | None = None


@dataclass(slots=True)
class _EventFeed:
    """Курсорное чтение журнала публикации в рамках одного turn-а.

    Читает строго по возрастанию ``id``; пустая страница курсор не двигает.
    Чужие команды отбрасываются по верхнеуровневому ``commandId``, а не по
    чему-либо внутри ``detail``. Дубли гасятся по ``detail['rcEventId']``.
    """

    publication_id: str
    command_id: str
    cursor: str | None = None
    steps: list[dict[str, str]] = field(default_factory=list)
    note: str = ""
    hint_state: str | None = None
    alerts: list[str] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    async def pull(self) -> None:
        """Тянет новую страницу журнала. Сбой чтения — косметика, не отказ."""
        try:
            payload = await herecrm_client.rc_events(
                self.publication_id,
                after_id=self.cursor,
                command_id=self.command_id,
            )
        except HereCrmClientError as error:
            log.info("журнал публикации недоступен (%s)", error.code)
            return
        parsed = remote_bridge.parse_events(payload)
        if not parsed:
            return
        for event in parsed:
            self._apply(event)
        self.cursor = remote_bridge.events_cursor(payload) or self.cursor

    def _apply(self, event: remote_bridge.RemoteEvent) -> None:
        if not remote_bridge.belongs_to(event, self.command_id):
            # Строки публикации (создана/закрыта) и события ЧУЖИХ команд на
            # этот turn не влияют, даже если публикация одна и та же.
            return
        rc_event_id = remote_bridge.runner_event_id(event)
        if rc_event_id is not None:
            if rc_event_id in self._seen:
                return
            self._seen.add(rc_event_id)
        step = remote_bridge.event_step(event)
        if step:
            self.steps.append(step)
            del self.steps[:-PROGRESS_STEPS_LIMIT]
        note = remote_bridge.event_note(event)
        if note:
            self.note = note
            if event.event_type == remote_bridge.EVENT_APPROVAL_REQUIRED:
                self.alerts.append(note)
            elif event.event_type == remote_bridge.EVENT_DIFF_SUMMARY:
                self.steps.append({"status": "ok", "desc": note})
        state = remote_bridge.event_command_state(event)
        if state:
            # Подсказка «можно перечитать команду», а не сам итог turn-а.
            self.hint_state = state

    def take_alerts(self) -> list[str]:
        pending, self.alerts = self.alerts, []
        return pending


def has_active_turn(key: ThreadKey) -> bool:
    """Идёт ли уже удалённый запуск в этом треде."""
    return any(not task.done() for task in _active.get(key, ()))


def start_remote_turn(bot: Bot, key: ThreadKey, conv: Any, run: QueuedRun) -> asyncio.Task[None]:
    """Запускает удалённый turn отдельной задачей и следит за её временем жизни."""
    queued = has_active_turn(key)
    task = asyncio.create_task(run_remote_turn(bot, conv, run, queued=queued))
    _active.setdefault(key, set()).add(task)
    task.add_done_callback(lambda finished: _forget(key, finished))
    return task


def _forget(key: ThreadKey, task: asyncio.Task[None]) -> None:
    tasks = _active.get(key)
    if tasks is None:
        return
    tasks.discard(task)
    if not tasks:
        _active.pop(key, None)


async def run_remote_turn(bot: Bot, conv: Any, run: QueuedRun, *, queued: bool = False) -> None:
    """Точка входа удалённого turn-а с единым журналом занятости бота."""
    runtime.mark_started()
    try:
        await _execute(bot, conv, run, queued=queued)
    except asyncio.CancelledError:
        raise
    except (HereCrmClientError, TelegramAPIError, OSError, sqlite3.Error) as error:
        log.warning("удалённый turn прерван (%s)", type(error).__name__)
        await _say(run.message, remote_bridge.refusal_text("rc_unavailable"))
    finally:
        runtime.mark_finished()


async def _execute(bot: Bot, conv: Any, run: QueuedRun, *, queued: bool) -> None:
    message = run.message
    binding = remote_bridge.conversation_binding(conv)
    device_label = binding.label

    if run.main_attachment or run.attachments:
        await _say(message, remote_bridge.refusal_text(remote_bridge.ATTACHMENTS_UNSUPPORTED))
        return
    if not run.text.strip():
        return
    if not herecrm_client.rc_configured():
        await _say(message, remote_bridge.refusal_text(remote_bridge.NOT_CONFIGURED))
        return

    publication = await _resolve_publication(message, conv, binding, capability="remotePrompt")
    if publication is None:
        return

    started_at = time.time()
    key = remote_bridge.idempotency_key(
        "prompt",
        message.chat.id,
        message.message_thread_id or 0,
        message.message_id,
    )
    command = await _create_prompt(message, conv, publication, run.text, key)
    if command is None:
        return

    # --- дальше команда УЖЕ принята сервером: ни один сбой ниже не должен
    # выглядеть как «не отправилось», иначе человек повторит запрос вручную ---
    _log_remote_turn(message, run.text, publication)

    if queued:
        await _say(
            message,
            "⏳ Устройство уже выполняет запрос — этот встал в его очередь. "
            "Прервать можно командой /rc stop.",
        )
    if not command.created:
        await _say(
            message,
            "↩️ Такой запрос уже был отправлен раньше — второй запуск не создан, "
            "показываю ход выполнения существующего.",
        )

    live = MessageLiveSession(
        bot=bot,
        source_message=message,
        model=None,
        account_label=None,
        account_notes=None,
        attachments=[],
        started_at=started_at,
        rich_stream_enabled=False,
        policy=_PROGRESS_POLICY,
        logger=log,
        device_label=device_label,
    )
    try:
        await live.start()
        outcome = await _poll_command(
            message,
            publication=publication,
            command=command,
            live=live,
            started_at=started_at,
        )
        await _deliver(bot, message, live, outcome, device_label=device_label, since=started_at)
    finally:
        await live.close()


async def _resolve_publication(
    message: Message,
    conv: Any,
    binding: remote_bridge.Binding,
    *,
    capability: str,
) -> remote_bridge.Publication | None:
    """Preflight: живую цель ищем ДО единственного POST, а не после."""
    try:
        # state=all: закрытую публикацию нужно видеть, иначе вместо точного
        # «публикация закрыта» человек получит расплывчатое «нет публикаций».
        payload = await herecrm_client.rc_publications(state="all")
    except HereCrmClientError as error:
        await _refuse(message, conv, error.code)
        return None
    selection = remote_bridge.select_target(
        remote_bridge.parse_publications(payload),
        binding.device_id,
        now=time.time(),
        capability=capability,
        conversation_id=binding.conversation_id,
    )
    if selection.publication is None:
        await _refuse(message, conv, selection.refusal or "rc_unavailable")
        return None
    return selection.publication


async def _create_prompt(
    message: Message,
    conv: Any,
    publication: remote_bridge.Publication,
    text: str,
    key: str,
) -> remote_bridge.RemoteCommand | None:
    """Ставит промпт. В payload уходит ТОЛЬКО текст запроса — ничего из Telegram."""
    last_code = "rc_unavailable"
    for attempt in range(CREATE_RETRIES + 1):
        try:
            response = await herecrm_client.rc_create_command(
                publication.id,
                command_type="prompt",
                payload={"prompt": text},
                idempotency_key=key,
            )
        except HereCrmClientError as error:
            last_code = error.code
            if error.code != "rc_unavailable" or attempt == CREATE_RETRIES:
                break
            log.warning("постановка промпта: сеть недоступна, повтор тем же ключом")
            await asyncio.sleep(POLL_INTERVAL_SEC)
            continue
        command = remote_bridge.parse_command(response)
        if command is not None:
            return command
        last_code = "rc_unavailable"
        break
    await _refuse(message, conv, last_code)
    return None


def _deadline(command: remote_bridge.RemoteCommand, started_at: float) -> float:
    """Потолок ожидания: срок жизни команды, но не больше собственного лимита."""
    hard = started_at + MAX_TURN_SEC
    if command.expires_at is None:
        return hard
    return min(command.expires_at + POLL_GRACE_SEC, hard)


async def _read_command(
    publication_id: str, command: remote_bridge.RemoteCommand
) -> remote_bridge.RemoteCommand | None:
    """Читает состояние команды. None = ответ непригоден, состояние прежнее."""
    response = await herecrm_client.rc_command_state(publication_id, command.id)
    parsed = remote_bridge.parse_command(response)
    if parsed is None or parsed.id != command.id:
        return None
    return parsed


async def _poll_command(
    message: Message,
    *,
    publication: remote_bridge.Publication,
    command: remote_bridge.RemoteCommand,
    live: MessageLiveSession,
    started_at: float,
) -> RemoteOutcome:
    """Опрашивает СТРОКУ команды до терминального статуса либо до потолка.

    Источник истины по завершению — только ``command.status``. Событие
    ``rc.command_status`` — эфемерная подсказка «можно перечитать команду», сама
    по себе turn она не закрывает.
    """
    feed = _EventFeed(publication_id=publication.id, command_id=command.id)
    interval = POLL_INTERVAL_SEC
    warned = False
    latest = command

    while True:
        terminal = remote_bridge.terminal_status(latest)
        if terminal:
            return RemoteOutcome(
                status=terminal,
                error_code=latest.error_code,
                crm_session_id=remote_bridge.crm_session_id(latest),
            )
        # Срок считаем по СВЕЖЕМУ состоянию: сервер мог продлить его при claim.
        # Собственный потолок всё равно ограничивает ожидание сверху.
        if time.time() >= _deadline(latest, started_at):
            break
        await feed.pull()
        await _push_events(message, feed)
        await _push_progress(live, latest.status, feed)
        await asyncio.sleep(interval)
        try:
            parsed = await _read_command(publication.id, latest)
        except HereCrmClientError as error:
            if error.code in ("rc_publication_closed", "rc_not_found"):
                # Сервер больше не ведёт эту команду: ждать нечего.
                return RemoteOutcome(status="expired", error_code=latest.error_code)
            # Обрыв связи с сервером не отменяет работу на устройстве.
            log.info("статус команды недоступен (%s), продолжаю опрос", error.code)
            interval = min(interval * 1.5, POLL_MAX_INTERVAL_SEC)
            continue
        if parsed is not None:
            latest = parsed
        interval = POLL_INTERVAL_SEC
        if (
            latest.status == "pending"
            and not warned
            and time.time() - started_at > CLAIM_WARN_AFTER_SEC
        ):
            warned = True
            await _say(
                message,
                "⌛ Устройство пока не забрало запрос. Продолжаю ждать — "
                "проверь, что чат HereAssistant на компьютере запущен.",
            )

    # Последняя попытка прочитать статус: команда могла завершиться в паузе.
    try:
        final = await _read_command(publication.id, latest)
    except HereCrmClientError as error:
        log.info("финальный статус команды недоступен (%s)", error.code)
        final = None
    if final is not None:
        latest = final
        terminal = remote_bridge.terminal_status(latest)
        if terminal:
            return RemoteOutcome(
                status=terminal,
                error_code=latest.error_code,
                crm_session_id=remote_bridge.crm_session_id(latest),
            )

    # Разница важна: просрочка — это отказ сервера вести команду дальше, а
    # собственный потолок означает, что работа на устройстве могла продолжиться.
    expires_at = latest.expires_at if latest.expires_at is not None else command.expires_at
    expired = expires_at is not None and time.time() >= expires_at
    return RemoteOutcome(status="expired" if expired else "timeout")


async def _push_events(message: Message, feed: _EventFeed) -> None:
    """Отдельные сообщения из журнала (запрос подтверждения) — по одному разу.

    Текст пришёл с устройства, поэтому экранируется: имя инструмента или причина
    со символом ``<`` иначе развалили бы HTML-разбор сообщения.
    """
    for alert in feed.take_alerts():
        await _say(message, html_escape(alert))


async def _push_progress(live: MessageLiveSession, status: str, feed: _EventFeed) -> None:
    """Обновление прогресса косметическое: его сбой не отменяет работу агента."""
    label = _PROGRESS_TEXT.get(status)
    if not label and not feed.steps and not feed.note:
        return
    partial = feed.note if feed.note else ""
    meta: dict[str, Any] = {"steps": list(feed.steps), "current_tool": label or status}
    try:
        await live.progress_callback(partial, "tool_use", meta)
    except TelegramAPIError as error:
        log.warning("не удалось обновить прогресс удалённого запуска: %s", error)


async def _deliver(
    bot: Bot,
    message: Message,
    live: MessageLiveSession,
    outcome: RemoteOutcome,
    *,
    device_label: str,
    since: float,
) -> None:
    """Финал: текст ответа берём только по точному id сессии CRM, иначе статус."""
    answer = None
    if outcome.status == "succeeded" and outcome.crm_session_id:
        answer = await _fetch_answer(outcome.crm_session_id, since)

    await live.stop_progress()
    header = f"💻 {html_escape(device_label)} · удалённо\n\n"

    if answer:
        payload = prepare_final_payload(
            answer,
            header_html=header,
            signature="",
            chain=[],
            rich_done=False,
            long_text_limit=3500,
            long_steps_limit=15,
            preview_limit=1500,
            timestamp=time.strftime("%H%M%S"),
        )
        html, attachments = payload.html, payload.attachments
    else:
        html, attachments = header + html_escape(_status_summary(outcome)), ()

    await FinalDelivery(
        bot=bot,
        source_message=message,
        progress=live.state,
        logger=log,
    ).deliver(
        FinalDeliveryRequest(
            html=html,
            rich_done=False,
            edits_markup=None,
            attachments=attachments,
            table_pngs=(),
            chat_id=message.chat.id,
            thread_id=message.message_thread_id or 0,
        )
    )


def _status_summary(outcome: RemoteOutcome) -> str:
    lines = [_STATUS_TEXT.get(outcome.status, "Запрос завершён")]
    if outcome.error_code:
        lines.append(f"Код: {outcome.error_code}")
        explanation = remote_bridge.error_code_text(outcome.error_code)
        if explanation:
            lines.append(explanation)
    if outcome.status in ("expired", "timeout"):
        lines.append(
            "Проверь чат HereAssistant на компьютере и состояние публикации — /rc status."
        )
    if outcome.status == "succeeded":
        # Текст ответа не показываем никогда, если его не отдала сама CRM:
        # угадывать чужую сессию нельзя, а придумывать содержимое — тем более.
        lines.append(
            "Текст ответа в CRM не пришёл: либо политика проекта на устройстве его "
            "не публикует, либо синхронизация ещё идёт. Смотри терминал на компьютере."
        )
        if config.HERECRM_WEB_URL:
            lines.append(config.HERECRM_WEB_URL)
    return "\n".join(lines)


async def _fetch_answer(crm_session_id: str, since: float) -> str | None:
    """Ищет ответ ассистента в ленте ИМЕННО этой сессии CRM.

    Лента — контур scopes ``sessions:read``: без них текста нет, и выдумывать его
    вместо честного статуса нельзя.
    """
    if not herecrm_client.configured():
        return None
    for attempt in range(FEED_ATTEMPTS):
        if attempt:
            await asyncio.sleep(FEED_INTERVAL_SEC)
        try:
            sessions = await herecrm_client.conversations()
            conversation_id = _match_session(sessions, crm_session_id)
            if conversation_id is None:
                continue
            page = await herecrm_client.feed(conversation_id, limit=20)
        except HereCrmClientError as error:
            log.info("лента сессии недоступна (%s)", error.code)
            continue
        text = _last_assistant_text(page, since)
        if text:
            return text
    return None


def _match_session(payload: Any, crm_session_id: str) -> str | None:
    for item in remote_bridge.list_items(payload):
        if not isinstance(item, Mapping):
            continue
        native = item.get("providerSessionId") or item.get("provider_session_id")
        if isinstance(native, str) and native == crm_session_id:
            return _string(item.get("id")) or None
    return None


def _last_assistant_text(page: Any, since: float) -> str | None:
    if not isinstance(page, Mapping):
        return None
    items = page.get("items")
    if not isinstance(items, list):
        return None
    for item in reversed(items):
        if not isinstance(item, Mapping) or item.get("kind") != "message":
            continue
        payload = item.get("message")
        if not isinstance(payload, Mapping) or payload.get("role") != "assistant":
            continue
        created = remote_bridge.parse_timestamp(payload.get("createdAt"))
        if created is not None and created + 5 < since:
            continue
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def _log_remote_turn(message: Message, text: str, publication: remote_bridge.Publication) -> None:
    """Журнал только метаданными: политику проекта считает устройство, не бот."""
    try:
        events.log(
            "message_in",
            user_id=message.from_user.id if message.from_user else None,
            chat_id=message.chat.id,
            thread_id=message.message_thread_id or 0,
            payload={"len": len(text), "remote": True, "device": publication.device_id},
        )
    except (sqlite3.Error, ValueError, TypeError) as error:
        log.warning("не удалось записать событие удалённого запуска (%s)", type(error).__name__)


async def _refuse(message: Message, conv: Any, code: str) -> None:
    """Понятный отказ вместо молчания; мёртвая привязка снимается сразу."""
    text = remote_bridge.refusal_text(code)
    if remote_bridge.should_unbind(code) and _detach(conv):
        text += (
            "\n\nПривязка треда к сессии устройства снята — сообщения снова уходят "
            "в серверную сессию бота."
        )
    await _say(message, text)


def _detach(conv: Any) -> bool:
    try:
        repo.set_remote_device(int(conv["id"]), None, None, None, None)
        return True
    except (sqlite3.Error, IndexError, KeyError, TypeError, ValueError) as error:
        log.warning("не удалось снять привязку устройства (%s)", type(error).__name__)
        return False


async def _say(message: Message, text: str) -> None:
    try:
        await message.answer(text, parse_mode="HTML")
    except TelegramAPIError as error:
        log.warning("не удалось ответить в чат: %s", error)


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
