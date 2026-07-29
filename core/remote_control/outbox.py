"""Исходящая очередь статусов/результатов /rc (rc_event_outbox).

Событие кладётся локально до отправки и покидает устройство только после
подтверждения сервера. Успешная доставка удаляет запись; сбой — экспоненциальный
retry. Payload собирается вызывающим кодом без prompt/содержимого.

Фоновый слив (``worker``/``flush_once``) повторяет подход ``core/crm_sync.py``
(``_next_due``/``_mark_delivered``/``_mark_retry``/``flush_once``/``worker``):
одно due-событие за цикл, не более одной успешной доставки на event_id,
необратимые операции (сам факт доставки) не повторяются вслепую.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

from .. import db

if TYPE_CHECKING:  # только для типов — рантайм-цикла в этом модуле нет
    from .control_plane_client import ControlPlaneClient

log = logging.getLogger("bridge.remote_control.outbox")

# Пауза между циклами слива, когда слать нечего (событие уже доставлено —
# следующая попытка сразу же, без паузы, как в crm_sync.worker).
_IDLE_INTERVAL_SEC = 2.0


def enqueue(
    payload: dict[str, Any],
    *,
    command_id: Optional[str] = None,
    publication_id: Optional[int] = None,
    event_id: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    """Идемпотентно кладёт событие в outbox; возвращает event_id или None."""
    event_id = event_id or str(uuid.uuid4())
    timestamp = int(now if now is not None else time.time())
    try:
        with db.conn() as connection:
            connection.execute(
                """INSERT INTO rc_event_outbox
                   (event_id, command_id, publication_id, payload, attempts,
                    next_attempt_at, created_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)
                   ON CONFLICT(event_id) DO NOTHING""",
                (
                    event_id,
                    command_id,
                    publication_id,
                    json.dumps(payload, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return event_id
    except (sqlite3.Error, TypeError, ValueError, OSError) as error:
        log.warning("RC outbox enqueue failed (%s)", type(error).__name__)
        return None


def next_due(*, now: Optional[int] = None) -> Optional[dict[str, Any]]:
    timestamp = int(now if now is not None else time.time())
    with db.conn() as connection:
        row = connection.execute(
            """SELECT event_id, command_id, payload, attempts
               FROM rc_event_outbox
               WHERE next_attempt_at <= ?
               ORDER BY created_at, event_id
               LIMIT 1""",
            (timestamp,),
        ).fetchone()
    return dict(row) if row else None


def mark_delivered(event_id: str) -> None:
    with db.conn() as connection:
        connection.execute("DELETE FROM rc_event_outbox WHERE event_id=?", (event_id,))


def mark_retry(event_id: str, attempts: int, reason: str, *, now: Optional[int] = None) -> None:
    timestamp = int(now if now is not None else time.time())
    next_attempts = attempts + 1
    delay = min(3600, 5 * (2 ** min(next_attempts, 9)))
    with db.conn() as connection:
        connection.execute(
            """UPDATE rc_event_outbox
               SET attempts=?, next_attempt_at=?, last_error=?
               WHERE event_id=?""",
            (next_attempts, timestamp + delay, reason[:120], event_id),
        )


def pending_count() -> int:
    with db.conn() as connection:
        row = connection.execute("SELECT COUNT(*) AS n FROM rc_event_outbox").fetchone()
    return int(row["n"]) if row else 0


async def flush_once(client: "ControlPlaneClient") -> bool:
    """Одна попытка доставки due-события. True — было что слать и это доставлено.

    ``client.configured()`` — тот же инвариант, что и у самого клиента: без URL
    и credential функция не делает ни одного обращения к ``next_due`` даже
    впустую по сети (просто нечего слать за пределы устройства).
    """
    if not client.configured():
        return False
    row = next_due()
    if row is None:
        return False
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError) as error:
        # Битый payload нельзя ни доставить, ни повторить осмысленно —
        # но и терять запись молча тоже нельзя: это retry, не delete.
        mark_retry(row["event_id"], row["attempts"], type(error).__name__)
        return False
    envelope: dict[str, Any] = {"eventId": row["event_id"], **payload}
    delivered = await client.post_result(envelope)
    if delivered:
        # Доставка уже состоялась. Если отметку записать не удалось, событие
        # останется в очереди и уйдёт повторно — сервер обязан быть идемпотентным
        # по eventId, но сам факт стоит явно засветить в логе, не молчать.
        try:
            mark_delivered(row["event_id"])
        except sqlite3.Error as error:
            log.error(
                "RC outbox доставлен, но не отмечен — возможен дубль event=%s: %s",
                row["event_id"],
                error,
            )
        log.info("RC outbox delivered event=%s", row["event_id"])
        return True
    mark_retry(row["event_id"], row["attempts"], "post_result_failed")
    return False


async def worker(client: "ControlPlaneClient", *, interval: float = _IDLE_INTERVAL_SEC) -> None:
    """Фоновая доставка outbox; неактивна, пока клиент не настроен (нет URL/credential).

    Никогда не блокирует терминальный чат: сбой цикла логируется и цикл
    продолжает работать со следующей паузой, как и ``core.crm_sync.worker``.
    """
    if not client.configured():
        log.info("RC outbox worker disabled (control-plane not configured)")
        return
    while True:
        try:
            delivered = await flush_once(client)
            await asyncio.sleep(0 if delivered else max(0.5, interval))
        except asyncio.CancelledError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OSError) as error:
            log.warning("RC outbox worker failed (%s)", type(error).__name__)
            await asyncio.sleep(max(0.5, interval))
