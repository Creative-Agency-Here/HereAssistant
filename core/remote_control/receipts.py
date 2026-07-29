"""Receipts удалённых команд и идемпотентность исполнения.

Receipt записывается в SQLite ДО запуска исполнения. Повторная доставка команды
с тем же command id не даёт второго исполнения: наличие receipt — защита от
дубля. Несовпадение payload hash при повторной доставке — fail closed (rejected).
Prompt/содержимое команды не хранится, только hash.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from .. import db

log = logging.getLogger("bridge.remote_control.receipts")

# Никаких shell/write_file/approve_tool: строго фиксированный набор типов команд.
ALLOWED_COMMAND_TYPES = frozenset(
    {"prompt", "stop", "approval_decision", "git_preflight", "git_commit", "git_push"}
)

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "rejected"})

# Что принимает сервер (``RunnerCommandResultDto.status``) — закрытый список из
# пяти значений. ``rejected`` там нет и добавлять его нельзя: enum виден витрине
# и фронту, а смысл отказа полностью выражается кодом причины.
SERVER_STATUSES = frozenset(
    {"running", "succeeded", "failed", "cancelled", "indeterminate"}
)

# Локальный словарь состояний ШИРЕ серверного намеренно: ``rejected`` остаётся в
# SQLite честной записью отказа, наружу он уезжает как ``failed`` плюс код
# причины. Иначе валидация вернула бы 400, статус не сохранился бы вовсе — и
# удалённая команда висела бы ``claimed`` до самого таймаута отправителя.
_SERVER_STATUS_BY_LOCAL_STATE = {
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "rejected": "failed",
    "running": "running",
    "indeterminate": "indeterminate",
}


def server_status(local_state: str) -> str:
    """Локальное состояние → статус из контракта сервера.

    Неизвестное состояние отображается в ``failed``: молчаливо отправить
    невалидное значение хуже, чем честно закрыть команду ошибкой.
    """
    return _SERVER_STATUS_BY_LOCAL_STATE.get(local_state, "failed")


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Итог попытки принять команду к исполнению."""

    should_execute: bool
    reason: str
    receipt: Optional[dict[str, Any]] = None


def get(command_id: str) -> Optional[dict[str, Any]]:
    with db.conn() as connection:
        row = connection.execute(
            "SELECT * FROM rc_command_receipts WHERE command_id=?", (command_id,)
        ).fetchone()
    return dict(row) if row else None


def claim(
    command_id: str,
    *,
    sequence: int,
    command_type: str,
    payload_hash: str,
    now: Optional[int] = None,
) -> ClaimResult:
    """Атомарно фиксирует receipt ДО исполнения и решает, исполнять ли команду.

    * неизвестный тип команды → fail closed, не исполняем;
    * тот же command id уже есть → не исполняем повторно (идемпотентность);
    * payload hash отличается от ранее принятого → fail closed (rejected).
    """
    if command_type not in ALLOWED_COMMAND_TYPES:
        log.warning("Отклонена команда неизвестного типа: %s", command_type)
        return ClaimResult(should_execute=False, reason="unknown_command_type")

    timestamp = int(now if now is not None else time.time())
    with db.conn() as connection:
        existing = connection.execute(
            "SELECT payload_hash, state FROM rc_command_receipts WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_hash"] != payload_hash:
                connection.execute(
                    """UPDATE rc_command_receipts
                       SET state='rejected', updated_at=?
                       WHERE command_id=?""",
                    (timestamp, command_id),
                )
                log.warning("Payload hash mismatch для command id — fail closed")
                return ClaimResult(should_execute=False, reason="payload_hash_mismatch")
            return ClaimResult(should_execute=False, reason="duplicate")

        connection.execute(
            """INSERT INTO rc_command_receipts
               (command_id, sequence, command_type, payload_hash, state,
                claimed_at, updated_at)
               VALUES (?, ?, ?, ?, 'claimed', ?, ?)""",
            (command_id, int(sequence), command_type, payload_hash, timestamp, timestamp),
        )
    return ClaimResult(should_execute=True, reason="claimed", receipt=get(command_id))


def mark_running(command_id: str, *, now: Optional[int] = None) -> None:
    timestamp = int(now if now is not None else time.time())
    with db.conn() as connection:
        connection.execute(
            """UPDATE rc_command_receipts
               SET state='running', started_at=COALESCE(started_at, ?), updated_at=?
               WHERE command_id=?""",
            (timestamp, timestamp, command_id),
        )


def finish(
    command_id: str,
    *,
    state: str,
    result_hash: Optional[str] = None,
    now: Optional[int] = None,
) -> None:
    if state not in TERMINAL_STATES:
        raise ValueError("Некорректное терминальное состояние receipt")
    timestamp = int(now if now is not None else time.time())
    with db.conn() as connection:
        connection.execute(
            """UPDATE rc_command_receipts
               SET state=?, result_hash=?, finished_at=?, updated_at=?
               WHERE command_id=?""",
            (state, result_hash, timestamp, timestamp, command_id),
        )
