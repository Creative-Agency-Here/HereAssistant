"""Ядро режима /rc: транспорт, локальное хранилище и privacy-гейты.

Публичный опенсорс-компонент без приватных доменов: базовый URL control-plane
по умолчанию пустой, режим выключен, пока владелец явно его не настроил.

Источник истины по командам — сервер (durable HTTPS claim/result/heartbeat).
Исходящий WSS используется только как уведомление «появились команды»; потеря
WS-соединения не теряет команду — её забирает следующий HTTPS reconcile.
"""

from __future__ import annotations

from . import config, credential_store, outbox, publications, receipts
from .control_plane_client import ControlPlaneClient, ControlPlaneError

__all__ = [
    "ControlPlaneClient",
    "ControlPlaneError",
    "config",
    "credential_store",
    "outbox",
    "publications",
    "receipts",
]
