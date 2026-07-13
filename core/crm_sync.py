"""Privacy-gated outbox transport HereAssistant -> HereCRM.

Сырой scoped-токен существует только в окружении процесса. SQLite содержит
лишь opt-in payload проектов с mode=crm; успешная доставка удаляет payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import aiohttp

from . import config, db, project_config

log = logging.getLogger("bridge.crm_sync")
_SESSION_NAMESPACE = uuid.UUID("821bb2cb-b5ee-45e1-a95d-d91f21f6ce20")


@dataclass(frozen=True, slots=True)
class Exchange:
    conversation_id: int
    telegram_user_id: int
    cwd: str
    project_name: str | None
    provider: str
    model: str | None
    prompt: str
    answer: str
    started_at: float
    finished_at: float
    tokens_in: int | None = None
    tokens_out: int | None = None
    duration_ms: int | None = None


def configured() -> bool:
    """Sync активен только полным набором URL + scoped has_ token."""
    return bool(config.HERECRM_SYNC_URL and config.HERECRM_SYNC_TOKEN)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _session_id(conversation_id: int) -> str:
    identity = f"{config.HERECRM_SYNC_ORIGIN}:{int(conversation_id)}"
    return str(uuid.uuid5(_SESSION_NAMESPACE, identity))


def build_payload(
    policy: project_config.ProjectPolicy,
    exchange: Exchange,
    *,
    event_id: str,
) -> dict[str, Any] | None:
    """Строит минимальный payload; default-deny возвращает None."""
    if not project_config.is_crm_visible(policy):
        return None

    messages: list[dict[str, str]] = []
    if project_config.can_sync_to_crm(policy, "prompts") and exchange.prompt:
        messages.append(
            {
                "role": "user",
                "content": exchange.prompt[:20000],
                "createdAt": _iso(exchange.started_at),
            }
        )
    if project_config.can_sync_to_crm(policy, "messages") and exchange.answer:
        messages.append(
            {
                "role": "assistant",
                "content": exchange.answer[:20000],
                "createdAt": _iso(exchange.finished_at),
            }
        )

    # Название из prompt допустимо только вместе с send_prompts. Иначе CRM
    # получает нейтральную метку, не фрагмент приватного запроса.
    title = (
        exchange.prompt.strip().replace("\n", " ")[:200]
        if project_config.can_sync_to_crm(policy, "prompts")
        else (policy.name or exchange.project_name or "HereAssistant session")[:200]
    )
    return {
        "eventId": event_id,
        "sessionId": _session_id(exchange.conversation_id),
        "telegramUserId": str(exchange.telegram_user_id),
        "provider": exchange.provider,
        "model": exchange.model,
        "cwd": exchange.cwd[:500],
        "projectName": (policy.name or exchange.project_name or None),
        "crmProjectId": policy.crm_project_id,
        "crmTaskId": policy.crm_task_id,
        "title": title,
        "createdAt": _iso(exchange.started_at),
        "lastActivityAt": _iso(exchange.finished_at),
        "messages": messages,
        "tokensIn": exchange.tokens_in,
        "tokensOut": exchange.tokens_out,
        "durationMs": exchange.duration_ms,
    }


def enqueue(policy: project_config.ProjectPolicy, exchange: Exchange) -> bool:
    """Атомарно кладёт разрешённое событие в outbox. Секрет здесь не участвует."""
    event_id = str(uuid.uuid4())
    payload = build_payload(policy, exchange, event_id=event_id)
    if payload is None:
        return False
    now = int(time.time())
    try:
        with db.conn() as connection:
            connection.execute(
                """INSERT INTO crm_sync_outbox
                   (event_id, user_id, conversation_id, payload, attempts,
                    next_attempt_at, created_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)""",
                (
                    event_id,
                    exchange.telegram_user_id,
                    exchange.conversation_id,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return True
    except (sqlite3.Error, TypeError, ValueError, OSError) as error:
        log.warning("CRM sync enqueue failed (%s)", type(error).__name__)
        return False


def _endpoint() -> str | None:
    if not configured():
        return None
    parsed = urlparse(config.HERECRM_SYNC_URL)
    if parsed.scheme != "https" or not parsed.netloc:
        log.error("HERECRM_SYNC_URL должен быть абсолютным https URL; sync выключен")
        return None
    return f"{config.HERECRM_SYNC_URL}/hereassistant-sync/events"


def _next_due() -> dict[str, Any] | None:
    with db.conn() as connection:
        row = connection.execute(
            """SELECT event_id, payload, attempts
               FROM crm_sync_outbox
               WHERE next_attempt_at <= ?
               ORDER BY created_at, event_id
               LIMIT 1""",
            (int(time.time()),),
        ).fetchone()
    return dict(row) if row else None


def _mark_delivered(event_id: str) -> None:
    with db.conn() as connection:
        connection.execute("DELETE FROM crm_sync_outbox WHERE event_id=?", (event_id,))


def _mark_retry(event_id: str, attempts: int, reason: str) -> None:
    next_attempts = attempts + 1
    delay = min(3600, 5 * (2 ** min(next_attempts, 9)))
    with db.conn() as connection:
        connection.execute(
            """UPDATE crm_sync_outbox
               SET attempts=?, next_attempt_at=?, last_error=?
               WHERE event_id=?""",
            (next_attempts, int(time.time()) + delay, reason[:120], event_id),
        )


async def flush_once(session: aiohttp.ClientSession) -> bool:
    endpoint = _endpoint()
    row = _next_due() if endpoint else None
    if not endpoint or not row:
        return False
    try:
        payload = json.loads(row["payload"])
        async with session.post(
            endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {config.HERECRM_SYNC_TOKEN}"},
        ) as response:
            if 200 <= response.status < 300:
                _mark_delivered(row["event_id"])
                log.info("CRM sync delivered event=%s", row["event_id"])
                return True
            _mark_retry(row["event_id"], row["attempts"], f"http:{response.status}")
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as error:
        _mark_retry(row["event_id"], row["attempts"], type(error).__name__)
    return False


async def worker() -> None:
    """Фоновая доставка; никогда не блокирует Telegram polling."""
    if not configured():
        log.info("HereCRM sync disabled (URL/token not configured)")
        return
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                delivered = await flush_once(session)
                await asyncio.sleep(0 if delivered else max(1.0, config.HERECRM_SYNC_INTERVAL_SEC))
            except asyncio.CancelledError:
                raise
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                json.JSONDecodeError,
                sqlite3.Error,
                OSError,
                TypeError,
                ValueError,
            ) as error:
                log.warning("CRM sync worker failed (%s)", type(error).__name__)
                await asyncio.sleep(max(1.0, config.HERECRM_SYNC_INTERVAL_SEC))


def status() -> dict[str, Any]:
    with db.conn() as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS pending, COALESCE(MAX(attempts), 0) AS max_attempts
               FROM crm_sync_outbox"""
        ).fetchone()
    return {
        "configured": configured(),
        "origin": config.HERECRM_SYNC_ORIGIN,
        "pending": int(row["pending"]),
        "max_attempts": int(row["max_attempts"]),
    }
