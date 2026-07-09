"""Проверка Telegram Mini App initData (HMAC-SHA256).

Стандарт: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Optional
from urllib.parse import parse_qsl

from core import config

import logging
log = logging.getLogger("webapp.auth")


def _admin_ids() -> set[int]:
    """Список Telegram-ID с доступом. Поддержка нового ADMIN_IDS=1,2 и легаси ADMIN_TELEGRAM_ID=1."""
    raw = os.environ.get("ADMIN_IDS", "").strip()
    ids: set[int] = set()
    if raw:
        for part in raw.split(","):
            p = part.strip()
            if p.lstrip("-").isdigit():
                ids.add(int(p))
    ids.update(config.ADMIN_IDS)
    return ids


def validate_init_data(init_data: str, max_age_sec: int = 86400) -> Optional[dict]:
    """Валидирует initData, возвращает распарсенный user-dict или None.
    Логирует причину отказа (видно в pm2-api логах)."""
    if not init_data:
        log.warning("auth: пустой initData (открыто не из Telegram?)")
        return None
    if not config.TELEGRAM_TOKEN:
        log.warning("auth: TELEGRAM_TOKEN не задан")
        return None

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    # ВАЖНО: исключаем ТОЛЬКО hash. Поле signature (Ed25519, новые клиенты)
    # ВХОДИТ в data-check-string для hash-проверки — проверено эмпирически на реальном initData.
    if not received_hash:
        log.warning("auth: нет hash; ключи=%s", list(pairs.keys()))
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", config.TELEGRAM_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        log.warning("auth: hash не сошёлся; ключи=%s", sorted(pairs.keys()))
        return None

    auth_date = pairs.get("auth_date")
    if auth_date and auth_date.isdigit():
        age = time.time() - int(auth_date)
        if age > max_age_sec:
            log.warning("auth: initData протух (%.0f сек > %d)", age, max_age_sec)
            return None

    user_raw = pairs.get("user")
    if not user_raw:
        log.warning("auth: нет поля user")
        return None
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        log.warning("auth: user не распарсился")
        return None

    tg_id = user.get("id")
    if not isinstance(tg_id, int) or tg_id not in _admin_ids():
        log.warning("auth: id=%s не в списке админов %s", tg_id, _admin_ids())
        return None

    return user
