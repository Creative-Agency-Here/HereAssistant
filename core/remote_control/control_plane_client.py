"""Клиент control-plane /rc: durable HTTPS + исходящий WSS-wakeup.

Источник истины по командам — сервер. Команды забираются через HTTPS claim
(reconcile), а WSS используется ТОЛЬКО как уведомление «появились команды».
Потеря WS-соединения не теряет команду: её заберёт следующий HTTPS reconcile.

Публичный компонент без приватных доменов: базовый URL по умолчанию пустой,
при пустом значении режим выключен.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import aiohttp

from . import config
from .credential_store import DeviceCredential

log = logging.getLogger("bridge.remote_control.client")


class ControlPlaneError(RuntimeError):
    """Безопасная ошибка control-plane (без тела ответа и секретов)."""

    def __init__(self, code: str, status: int = 502):
        super().__init__(code)
        self.code = code
        self.status = status


class ControlPlaneClient:
    """Durable HTTPS операции: claim команд, доставка результатов, heartbeat."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        credential: Optional[DeviceCredential] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else config.control_plane_url()).rstrip("/")
        self._credential = credential
        self._external_session = session

    def configured(self) -> bool:
        """Режим активен только при явном абсолютном https URL."""
        if not self._base_url:
            return False
        parsed = urlparse(self._base_url)
        return parsed.scheme == "https" and bool(parsed.netloc)

    def _endpoint(self, path: str) -> str:
        if not self.configured():
            raise ControlPlaneError("rc_not_configured", 503)
        return f"{self._base_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        if self._credential and self._credential.token:
            return {"Authorization": f"Bearer {self._credential.token}"}
        return {}

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        timeout = aiohttp.ClientTimeout(total=15)
        owns_session = self._external_session is None
        session = self._external_session or aiohttp.ClientSession(timeout=timeout)
        try:
            async with session.post(
                self._endpoint(path), json=payload, headers=self._headers()
            ) as response:
                if response.status in (401, 403):
                    raise ControlPlaneError("rc_unauthorized", response.status)
                if response.status >= 400:
                    raise ControlPlaneError("rc_unavailable", response.status)
                try:
                    return await response.json()
                except (aiohttp.ContentTypeError, ValueError) as error:
                    raise ControlPlaneError("rc_invalid_response", 502) from error
        except ControlPlaneError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ControlPlaneError("rc_unavailable", 502) from error
        finally:
            if owns_session:
                await session.close()

    async def claim_pending(self, *, device_id: str, last_sequence: int = 0) -> list[dict[str, Any]]:
        """Забирает pending-команды с сервера (источник истины).

        Вызывается по reconcile и после WS-wakeup. Возвращает список подписанных
        envelope; пустой список — норма (команд нет или сервер недоступен).
        """
        try:
            result = await self._post(
                "rc/commands/claim",
                {"deviceId": device_id, "lastSequence": int(last_sequence)},
            )
        except ControlPlaneError as error:
            log.warning("RC claim недоступен (%s)", error.code)
            return []
        commands = result.get("commands") if isinstance(result, dict) else None
        return commands if isinstance(commands, list) else []

    async def post_result(self, event: dict[str, Any]) -> bool:
        """Доставляет статус/результат события. True — сервер подтвердил."""
        try:
            await self._post("rc/events", event)
            return True
        except ControlPlaneError as error:
            log.warning("RC result не доставлен (%s)", error.code)
            return False

    async def heartbeat(self, *, device_id: str, publication_id: str, state: str) -> bool:
        try:
            await self._post(
                "rc/heartbeat",
                {"deviceId": device_id, "publicationId": publication_id, "state": state},
            )
            return True
        except ControlPlaneError as error:
            log.warning("RC heartbeat не доставлен (%s)", error.code)
            return False


class WakeupListener:
    """Исходящий Socket.IO клиент только для уведомлений о новых командах.

    Не является каналом передачи команд: при недоступности python-socketio или
    потере соединения корректность не страдает — команды забирает HTTPS claim.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        credential: Optional[DeviceCredential] = None,
        on_commands_available: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else config.control_plane_url()).rstrip("/")
        self._credential = credential
        self._on_commands_available = on_commands_available
        self._client = None
        self.available = False

    async def start(self) -> bool:
        """Пытается подключить WS-wakeup. False — wakeup недоступен (не фатально)."""
        try:
            import socketio  # type: ignore
        except ImportError:
            log.info("python-socketio не установлен — /rc работает на HTTPS reconcile")
            return False
        if not self._base_url:
            return False

        client = socketio.AsyncClient(reconnection=True)

        @client.on("rc:command:available")
        async def _on_available(_data: Any = None) -> None:
            # WS лишь будит: фактический забор команд делает HTTPS reconcile.
            if self._on_commands_available is not None:
                try:
                    await self._on_commands_available()
                except (OSError, RuntimeError, ValueError, asyncio.TimeoutError) as error:
                    log.warning("RC reconcile callback failed (%s)", type(error).__name__)

        self._client = client
        self.available = True
        return True

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except (OSError, RuntimeError, asyncio.TimeoutError) as error:
                # Отключение best effort: важен факт закрытия, не его успех.
                log.debug("WS disconnect: %s", error)
            self._client = None
        self.available = False
