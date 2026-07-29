"""Состояние публикации /rc и privacy-gated presence payload.

Публикация создаётся только явным действием владельца и живёт по TTL. Наружу
уходит лишь то, что разрешает privacy policy: для приватного проекта — только
presence без пути, имени проекта, repo и любого содержимого сессии.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Optional

from .. import db, project_config

log = logging.getLogger("bridge.remote_control.publications")

STATES = frozenset(
    {
        "unpublished",
        "published_idle",
        "queued",
        "running",
        "awaiting_local_approval",
        "offline",
        "expired",
        "revoked",
        "closed",
        "failed",
    }
)


@dataclass(frozen=True, slots=True)
class LocalSessionMeta:
    """Чувствительные локальные метки сессии. Покидают устройство не всегда."""

    cwd: str
    project_name: Optional[str] = None
    repo: Optional[str] = None
    provider_session_id: Optional[str] = None


def compile_capabilities(policy: project_config.ProjectPolicy) -> dict[str, bool]:
    """Снимок возможностей, разрешённых политикой. Для private всё False."""
    return {
        "remotePrompt": project_config.can_receive_remote_prompts(policy),
        "messages": project_config.can_stream_rc_messages(policy),
        "diffs": project_config.can_stream_rc_diffs(policy),
        "commits": project_config.can_stream_rc_commits(policy),
        "git": project_config.can_execute_rc_git(policy),
    }


def publish(
    local_session_key: str,
    *,
    policy: project_config.ProjectPolicy,
    device_id: str,
    remote_public_id: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Создаёт/обновляет публикацию, если политика разрешает presence.

    Возвращает строку публикации или None при запрете (default deny).
    """
    if not project_config.can_publish_rc_presence(policy):
        return None
    timestamp = int(now if now is not None else time.time())
    privacy_mode = "crm" if policy.mode == "crm" else "private"
    capabilities = json.dumps(compile_capabilities(policy), separators=(",", ":"))
    expires_at = timestamp + max(5, policy.rc_ttl_minutes) * 60
    with db.conn() as connection:
        existing = connection.execute(
            "SELECT generation FROM rc_publications WHERE local_session_key=?",
            (local_session_key,),
        ).fetchone()
        generation = (int(existing["generation"]) + 1) if existing else 1
        connection.execute(
            """INSERT INTO rc_publications
               (local_session_key, remote_public_id, device_id, privacy_mode,
                capabilities_json, generation, last_sequence, state,
                published_at, last_heartbeat_at, expires_at, closed_at,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, 'published_idle', ?, ?, ?, NULL, ?, ?)
               ON CONFLICT(local_session_key) DO UPDATE SET
                   remote_public_id=excluded.remote_public_id,
                   device_id=excluded.device_id,
                   privacy_mode=excluded.privacy_mode,
                   capabilities_json=excluded.capabilities_json,
                   generation=excluded.generation,
                   last_sequence=0,
                   state='published_idle',
                   published_at=excluded.published_at,
                   last_heartbeat_at=excluded.last_heartbeat_at,
                   expires_at=excluded.expires_at,
                   closed_at=NULL,
                   updated_at=excluded.updated_at""",
            (
                local_session_key,
                remote_public_id,
                device_id,
                privacy_mode,
                capabilities,
                generation,
                timestamp,
                timestamp,
                expires_at,
                timestamp,
                timestamp,
            ),
        )
    return get(local_session_key)


def attach_remote_id(local_session_key: str, remote_public_id: str) -> None:
    """Сохраняет серверный идентификатор публикации.

    До этого момента адресовать команды, heartbeat и события нечем: сервер
    работает только по своему UUID публикации.
    """
    with db.conn() as connection:
        connection.execute(
            "UPDATE rc_publications SET remote_public_id=?, updated_at=? "
            "WHERE local_session_key=?",
            (str(remote_public_id), int(time.time()), local_session_key),
        )


def get(local_session_key: str) -> Optional[dict[str, Any]]:
    with db.conn() as connection:
        row = connection.execute(
            "SELECT * FROM rc_publications WHERE local_session_key=?",
            (local_session_key,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def set_state(local_session_key: str, state: str, *, now: Optional[int] = None) -> None:
    if state not in STATES:
        raise ValueError("Некорректное состояние публикации")
    timestamp = int(now if now is not None else time.time())
    closed_at = timestamp if state in ("closed", "revoked", "expired") else None
    with db.conn() as connection:
        connection.execute(
            """UPDATE rc_publications
               SET state=?, closed_at=COALESCE(?, closed_at), updated_at=?
               WHERE local_session_key=?""",
            (state, closed_at, timestamp, local_session_key),
        )


def record_heartbeat(local_session_key: str, *, now: Optional[int] = None) -> None:
    timestamp = int(now if now is not None else time.time())
    with db.conn() as connection:
        connection.execute(
            "UPDATE rc_publications SET last_heartbeat_at=?, updated_at=? WHERE local_session_key=?",
            (timestamp, timestamp, local_session_key),
        )


def advance_sequence(local_session_key: str, sequence: int) -> None:
    """Monotonic server sequence; меньшие значения игнорируются."""
    timestamp = int(time.time())
    with db.conn() as connection:
        connection.execute(
            """UPDATE rc_publications
               SET last_sequence=MAX(last_sequence, ?), updated_at=?
               WHERE local_session_key=?""",
            (int(sequence), timestamp, local_session_key),
        )


def close(local_session_key: str, *, now: Optional[int] = None) -> None:
    set_state(local_session_key, "closed", now=now)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["capabilities"] = json.loads(data.get("capabilities_json") or "{}")
    except ValueError:
        data["capabilities"] = {}
    return data


def presence_payload(
    policy: project_config.ProjectPolicy,
    publication: dict[str, Any],
    *,
    device_name: str,
    device_kind: str,
    meta: LocalSessionMeta,
) -> Optional[dict[str, Any]]:
    """Строит наружный presence payload строго по политике.

    Для private: только opaque id, устройство, состояние, expiry и capabilities
    (все False). Никаких cwd/project_name/repo/provider_session/transcript.
    Для crm: добавляется имя проекта из политики (не абсолютный путь).
    """
    if not project_config.can_publish_rc_presence(policy):
        return None

    capabilities = compile_capabilities(policy)
    payload: dict[str, Any] = {
        "publicationId": publication.get("remote_public_id")
        or publication.get("local_session_key"),
        "privacyMode": "crm" if policy.mode == "crm" else "private",
        "deviceId": publication.get("device_id"),
        "deviceName": device_name[:120],
        "deviceKind": device_kind[:40],
        "state": publication.get("state"),
        "generation": publication.get("generation"),
        "expiresAt": publication.get("expires_at"),
        "capabilities": capabilities,
    }

    if policy.mode == "crm":
        # Имя проекта из политики — не абсолютный локальный путь.
        if policy.name:
            payload["projectName"] = policy.name[:200]
        # meta.repo здесь намеренно не используется: абсолютный cwd и локальный
        # provider session id наружу не уходят никогда.
    return payload
