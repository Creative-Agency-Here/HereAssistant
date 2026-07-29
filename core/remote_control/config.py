"""Конфигурация control-plane /rc. Default-deny и никаких приватных доменов.

Базовый URL берётся только из окружения процесса и по умолчанию пустой: пока
владелец явно не задал адрес, режим считается выключенным. В публичной поставке
нет ни одного домена по умолчанию.
"""

from __future__ import annotations

import os

# Интервалы транспорта (сек). Публичные константы, не приватная топология.
HEARTBEAT_INTERVAL_SEC: float = 15.0
OFFLINE_AFTER_SEC: float = 45.0
RECONCILE_INTERVAL_SEC: float = 30.0


def control_plane_url() -> str:
    """Базовый https URL control-plane. Пустая строка = режим выключен."""
    return os.environ.get("RC_CONTROL_PLANE_URL", "").strip().rstrip("/")


def configured() -> bool:
    """Режим активен только при явно заданном непустом URL."""
    return bool(control_plane_url())
