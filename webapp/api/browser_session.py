"""Подписанная HttpOnly-сессия браузера после SSO из HereCRM."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from core import config

COOKIE_NAME = "ha_crm_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signing_key() -> bytes:
    if not config.HERECRM_SYNC_TOKEN:
        return b""
    return hmac.new(
        config.HERECRM_SYNC_TOKEN.encode("utf-8"),
        b"hereassistant-browser-session-v1",
        hashlib.sha256,
    ).digest()


def issue(*, crm_user_id: int, tenant_id: str) -> str:
    """Создаёт подписанную сессию; ротация sync-токена инвалидирует её."""
    key = _signing_key()
    if not key or config.ADMIN_ID is None:
        raise RuntimeError("crm_sso_not_configured")
    payload = {
        "v": 1,
        "crm_user_id": int(crm_user_id),
        "tenant_id": str(tenant_id),
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    encoded = _encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _encode(hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def read(token: str) -> dict[str, Any] | None:
    """Проверяет подпись и возвращает локальную owner-identity для API."""
    key = _signing_key()
    if not key or not token or len(token) > 2048 or config.ADMIN_ID is None:
        return None
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_decode(signature), expected):
            return None
        payload = json.loads(_decode(encoded))
        if (
            payload.get("v") != 1
            or int(payload.get("exp", 0)) <= int(time.time())
            or int(payload.get("crm_user_id", 0)) <= 0
            or not str(payload.get("tenant_id", ""))
        ):
            return None
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return {
        "id": config.ADMIN_ID,
        "first_name": "HereCRM",
        "username": "herecrm",
        "auth_source": "crm",
        "crm_user_id": int(payload["crm_user_id"]),
        "tenant_id": str(payload["tenant_id"]),
    }
