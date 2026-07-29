"""Исходящие события прогресса /rc: сборка, privacy-фильтрация, постановка в outbox.

Локальный источник событий этапа P4. Каждый тип события проходит СВОЙ гейт
(default deny): текст ответа — ``can_stream_rc_messages``, сводка правок —
``can_stream_rc_diffs``, метаданные коммита — ``can_stream_rc_commits``. Факт
смены статуса команды — единственное, что разрешено приватному проекту (гейт
``can_publish_rc_presence``): без текста, путей, имени проекта и рабочей папки.

Наружу никогда не уходят абсолютные домашние пути, переменные окружения, сырой
stdout/stderr, содержимое файлов, аргументы командной строки и токены. Пути
инструментов нормализуются до относительных от корня проекта. Длинные значения
обрезаются с пометкой.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from .. import project_config
from . import outbox

log = logging.getLogger("bridge.remote_control.events")

# Фиксированный набор типов событий этапа P4.
TYPE_PROGRESS = "rc.progress"
TYPE_TOOL_CALL = "rc.tool_call"
TYPE_APPROVAL_REQUIRED = "rc.approval_required"
TYPE_DIFF_SUMMARY = "rc.diff_summary"
TYPE_COMMAND_STATUS = "rc.command_status"

# Пределы размера: событие не должно уносить много текста.
MAX_TEXT_CHARS = 800
MAX_SHORT_CHARS = 200
MAX_PATHS = 50
TRUNCATION_MARK = "…[обрезано]"

# Состояния, при которых turn закончен: только в них имеет смысл подсказывать
# интерфейсу, где искать полный текст ответа (``crmSessionId``).
TERMINAL_EVENT_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "rejected", "indeterminate"}
)


def _truncate(text: str, limit: int) -> str:
    """Обрезает длинный текст, оставляя пометку об обрезке."""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNCATION_MARK))
    return text[:keep] + TRUNCATION_MARK


def _scrub_home(text: str) -> str:
    """Заменяет абсолютный домашний путь на ~, чтобы он не покинул устройство."""
    try:
        home = str(Path.home())
    except (OSError, RuntimeError, ValueError):
        return text
    if home and home not in ("/", "") and home in text:
        return text.replace(home, "~")
    return text


def _normalize_tool(tool: str) -> str:
    """Оставляет в имени инструмента только безопасные символы."""
    cleaned = "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in str(tool))
    return cleaned[:MAX_SHORT_CHARS] or "tool"


def _as_count(value: object) -> int:
    """Неотрицательный целый счётчик; всё подозрительное становится нулём."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _relative_path(path: Optional[str], project_root: Optional[str]) -> Optional[str]:
    """Возвращает путь ОТНОСИТЕЛЬНО корня проекта или None.

    Абсолютные пути вне корня (включая домашний каталог) наружу не уходят:
    вместо утечки возвращается None. Относительный путь нормализуется как есть.
    """
    if not path or not project_root:
        return None
    try:
        candidate = Path(path)
        if candidate.is_absolute():
            relative = candidate.resolve().relative_to(Path(project_root).resolve())
        else:
            relative = candidate
        if ".." in relative.parts:
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return relative.as_posix()


def emit_command_status(
    policy: project_config.ProjectPolicy,
    *,
    command_id: Optional[str],
    state: str,
    publication_id: Optional[int] = None,
    commit_sha: Optional[str] = None,
    commit_message: Optional[str] = None,
    crm_session_id: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    """Факт смены статуса команды — единственное, что видит приватный проект.

    Метаданные коммита добавляются только при отдельном явном флаге
    ``can_stream_rc_commits``; в приватном режиме их нет никогда.

    ``crmSessionId`` — не содержимое, а адрес ленты: по нему интерфейс находит
    полный текст ответа в CRM (у control-plane текста нет и быть не должно).
    Уходит он только при терминальном состоянии turn-а и только когда проект и
    так синхронизирует сообщения в CRM (``can_sync_to_crm(policy, "messages")``);
    приватный проект своего идентификатора не отдаёт никогда.
    """
    if not project_config.can_publish_rc_presence(policy):
        return None
    payload: dict[str, Any] = {
        "type": TYPE_COMMAND_STATUS,
        "commandId": command_id,
        "state": state,
    }
    if (
        crm_session_id
        and state in TERMINAL_EVENT_STATES
        and project_config.can_sync_to_crm(policy, "messages")
    ):
        payload["crmSessionId"] = str(crm_session_id)[:MAX_SHORT_CHARS]
    if commit_sha and project_config.can_stream_rc_commits(policy):
        payload["commitSha"] = _scrub_home(str(commit_sha))[:MAX_SHORT_CHARS]
        if commit_message:
            payload["commitMessage"] = _truncate(
                _scrub_home(str(commit_message)), MAX_SHORT_CHARS
            )
    return outbox.enqueue(payload, command_id=command_id, publication_id=publication_id, now=now)


def emit_progress(
    policy: project_config.ProjectPolicy,
    *,
    command_id: Optional[str],
    text: str,
    publication_id: Optional[int] = None,
    state: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    """Кусок ответа ассистента. Уходит только при явном флаге стриминга сообщений.

    ЗАРЕЗЕРВИРОВАНО: production-вызывающего у этой функции сейчас нет намеренно,
    и её отсутствие в ленте — не пропущенная проводка.

    Во-первых, живого потока ответа на устройстве нет: провайдеры запускаются
    неинтерактивно, промежуточных кусков текста координатору никто не отдаёт, а
    события уезжают пачками по циклу heartbeat. Имитировать поток нельзя — лента
    в Telegram обновляется дискретно, и подписи в интерфейсе это признают.

    Во-вторых, полный текст ответа берётся из ленты CRM по ``crmSessionId`` из
    результата команды, то есть по контуру ``sessions:read``. Копировать тот же
    текст ещё и в события значило бы сохранять ответы ассистента в
    ``cli_agent_remote_audit.detail`` на control-plane, где retention пока не
    реализован, — то есть расширять privacy-след без единой новой возможности.

    Функция и тип ``rc.progress`` остаются частью контракта: их разбирает читатель
    (``core/remote_bridge.event_note``), и они пригодятся, как только у провайдера
    появится настоящий инкрементальный вывод. Гейт остаётся default deny.
    """
    if not project_config.can_stream_rc_messages(policy):
        return None
    payload: dict[str, Any] = {
        "type": TYPE_PROGRESS,
        "commandId": command_id,
        "text": _truncate(_scrub_home(str(text)), MAX_TEXT_CHARS),
    }
    if state:
        payload["state"] = state
    return outbox.enqueue(payload, command_id=command_id, publication_id=publication_id, now=now)


def emit_tool_call(
    policy: project_config.ProjectPolicy,
    *,
    command_id: Optional[str],
    tool: str,
    status: str,
    publication_id: Optional[int] = None,
    path: Optional[str] = None,
    project_root: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    """Вызов инструмента: только нормализованные тип, статус и относительный путь."""
    if not project_config.can_stream_rc_messages(policy):
        return None
    payload: dict[str, Any] = {
        "type": TYPE_TOOL_CALL,
        "commandId": command_id,
        "tool": _normalize_tool(tool),
        "status": status,
    }
    relative = _relative_path(path, project_root)
    if relative:
        payload["path"] = relative
    return outbox.enqueue(payload, command_id=command_id, publication_id=publication_id, now=now)


def emit_approval_required(
    policy: project_config.ProjectPolicy,
    *,
    command_id: Optional[str],
    tool: str,
    publication_id: Optional[int] = None,
    reason: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    """Запрошено подтверждение. Нормализованный инструмент, без аргументов команды."""
    if not project_config.can_stream_rc_messages(policy):
        return None
    payload: dict[str, Any] = {
        "type": TYPE_APPROVAL_REQUIRED,
        "commandId": command_id,
        "tool": _normalize_tool(tool),
    }
    if reason:
        payload["reason"] = _truncate(_scrub_home(str(reason)), MAX_SHORT_CHARS)
    return outbox.enqueue(payload, command_id=command_id, publication_id=publication_id, now=now)


def emit_diff_summary(
    policy: project_config.ProjectPolicy,
    *,
    command_id: Optional[str],
    publication_id: Optional[int] = None,
    files_changed: int = 0,
    insertions: int = 0,
    deletions: int = 0,
    paths: Optional[list[str]] = None,
    project_root: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    """Сводка правок: счётчики и относительные пути. Без содержимого диффа."""
    if not project_config.can_stream_rc_diffs(policy):
        return None
    payload: dict[str, Any] = {
        "type": TYPE_DIFF_SUMMARY,
        "commandId": command_id,
        "filesChanged": _as_count(files_changed),
        "insertions": _as_count(insertions),
        "deletions": _as_count(deletions),
    }
    if paths:
        relative = [item for item in (_relative_path(p, project_root) for p in paths) if item]
        if relative:
            payload["paths"] = relative[:MAX_PATHS]
    return outbox.enqueue(payload, command_id=command_id, publication_id=publication_id, now=now)
