"""Клиент control-plane /rc: durable HTTPS + исходящий WSS-wakeup.

Источник истины по командам — сервер. Команды забираются через HTTPS (список +
поштучный claim), а WSS используется ТОЛЬКО как уведомление «появились команды».
Потеря WS-соединения не теряет команду: её заберёт следующий HTTPS reconcile.

Реальный контракт сервера — контроллер ``cli-agent/runner`` (Admin Panel,
``remote-control.runner.controller.ts``): базовый префикс маршрутов
``cli-agent/runner``, аутентификация — короткий device access-токен, выдаваемый
обменом raw credential (``harc_…``) на токен через ``POST exchange``. Raw
credential никогда не уходит на защищённые маршруты как Bearer — только в теле
запроса на обмен, и никогда не логируется.

Публичный компонент без приватных доменов: базовый URL по умолчанию пустой,
при пустом значении режим выключен.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import aiohttp

from . import config
from .credential_store import DeviceCredential

log = logging.getLogger("bridge.remote_control.client")

# Базовый префикс контроллера раннера на control-plane (источник истины —
# remote-control.runner.controller.ts, @Controller('cli-agent/runner')).
_RUNNER_PREFIX = "cli-agent/runner"

# Запас времени перед истечением access-токена, чтобы не словить 401 из-за
# скоса часов или задержки в полёте запроса — обновляем чуть заранее.
_TOKEN_REFRESH_MARGIN_SEC = 30.0


class ControlPlaneError(RuntimeError):
    """Безопасная ошибка control-plane (без тела ответа и секретов)."""

    def __init__(self, code: str, status: int = 502):
        super().__init__(code)
        self.code = code
        self.status = status


def _parse_expiry(value: object) -> float:
    """Момент истечения access-токена в epoch-секундах (с запасом на скос часов).

    Не удалось разобрать срок — токен не кэшируется между вызовами (следующее
    обращение обменяет credential заново); сам факт использования токена в
    текущем запросе это не затрагивает.
    """
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return parsed.timestamp() - _TOKEN_REFRESH_MARGIN_SEC
    return 0.0


class ControlPlaneClient:
    """Durable HTTPS операции раннера: exchange, публикации, claim, результаты, события."""

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
        # Короткоживущий device access-токен — только в памяти процесса, никогда
        # не пишется в SQLite/лог/argv. Обновляется через exchange по истечении.
        self._access_token: Optional[str] = None
        self._access_token_expires_at: float = 0.0

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

    # ---------- аутентификация: обмен credential → короткий access-токен ----------

    async def _ensure_access_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        """Валидный access-токен: из кеша либо через свежий exchange."""
        if self._credential is None:
            return None
        if self._access_token and time.time() < self._access_token_expires_at:
            return self._access_token
        return await self._exchange(session)

    async def _exchange(self, session: aiohttp.ClientSession) -> Optional[str]:
        """Обменивает raw credential на короткий access-токен (без Authorization)."""
        if self._credential is None:
            return None
        url = self._endpoint(f"{_RUNNER_PREFIX}/exchange")
        try:
            async with session.post(
                url, json={"credential": self._credential.token}, headers={}
            ) as response:
                if response.status in (401, 403):
                    raise ControlPlaneError("rc_unauthorized", response.status)
                if response.status >= 400:
                    raise ControlPlaneError("rc_exchange_failed", response.status)
                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as error:
                    raise ControlPlaneError("rc_invalid_response", 502) from error
        except ControlPlaneError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ControlPlaneError("rc_unavailable", 502) from error
        if not isinstance(data, dict):
            raise ControlPlaneError("rc_invalid_response", 502)
        access_token = data.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise ControlPlaneError("rc_invalid_response", 502)
        self._access_token = access_token
        self._access_token_expires_at = _parse_expiry(data.get("expiresAt"))
        return access_token

    def _invalidate_access_token(self) -> None:
        self._access_token = None
        self._access_token_expires_at = 0.0

    # ---------- низкоуровневый транспорт ----------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        auth: bool = True,
    ) -> Any:
        """Один HTTP-вызов раннера. При 401/403 на авторизованном вызове — один
        принудительный повтор со свежим access-токеном (истёкший токен не должен
        ронять цикл reconcile/heartbeat), затем — явная ошибка.
        """
        timeout = aiohttp.ClientTimeout(total=15)
        owns_session = self._external_session is None
        session = self._external_session or aiohttp.ClientSession(timeout=timeout)
        try:
            for attempt in (1, 2):
                headers: dict[str, str] = {}
                if auth:
                    token = await self._ensure_access_token(session)
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                request_kwargs: dict[str, Any] = {"headers": headers}
                if json_body is not None:
                    request_kwargs["json"] = json_body
                if params is not None:
                    request_kwargs["params"] = params
                caller = session.get if method == "GET" else session.post
                async with caller(self._endpoint(path), **request_kwargs) as response:
                    if response.status in (401, 403):
                        if auth and attempt == 1 and self._credential is not None:
                            self._invalidate_access_token()
                            continue
                        raise ControlPlaneError("rc_unauthorized", response.status)
                    if response.status >= 400:
                        raise ControlPlaneError("rc_unavailable", response.status)
                    try:
                        return await response.json()
                    except (aiohttp.ContentTypeError, ValueError) as error:
                        raise ControlPlaneError("rc_invalid_response", 502) from error
            # Недостижимо: цикл выше либо возвращает, либо бросает на второй попытке.
            raise ControlPlaneError("rc_unauthorized", 401)
        except ControlPlaneError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ControlPlaneError("rc_unavailable", 502) from error
        finally:
            if owns_session:
                await session.close()

    # ---------- операции раннера (cli-agent/runner/publications/:id/...) ----------

    async def create_publication(
        self,
        *,
        public_id: str,
        privacy_mode: str,
        capabilities: Optional[dict[str, Any]] = None,
        ttl_minutes: Optional[int] = None,
    ) -> Optional[str]:
        """Публикует сессию на сервере и возвращает серверный UUID публикации.

        Без него все остальные операции раннера невозможны: сервер адресует
        команды, heartbeat и события именно по этому идентификатору.
        Поля тела — ровно из ``RunnerPublishDto`` контроллера ``cli-agent/runner``.
        """
        payload: dict[str, Any] = {"publicId": public_id, "privacyMode": privacy_mode}
        if capabilities is not None:
            payload["capabilities"] = capabilities
        if ttl_minutes is not None:
            payload["ttlMinutes"] = int(ttl_minutes)
        try:
            result = await self._request(
                "POST", f"{_RUNNER_PREFIX}/publications", payload=payload
            )
        except ControlPlaneError as error:
            log.warning("RC публикация не создана (%s)", error.code)
            return None
        if isinstance(result, dict):
            publication_id = result.get("id") or result.get("publicationId")
            if isinstance(publication_id, str) and publication_id:
                return publication_id
        return None

    async def close_publication(self, *, publication_id: str) -> bool:
        """Снимает публикацию на сервере (``/rc off`` и выход из чата)."""
        try:
            await self._request(
                "DELETE", f"{_RUNNER_PREFIX}/publications/{publication_id}"
            )
        except ControlPlaneError as error:
            log.warning("RC публикация не закрыта (%s)", error.code)
            return False
        return True

    async def list_commands(
        self, *, publication_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        """Команды публикации после ``after_sequence`` (GET, без claim — reconcile)."""
        try:
            result = await self._request(
                "GET",
                f"{_RUNNER_PREFIX}/publications/{publication_id}/commands",
                params={"afterSequence": str(max(0, int(after_sequence)))},
            )
        except ControlPlaneError as error:
            log.warning("RC список команд недоступен (%s)", error.code)
            return []
        return result if isinstance(result, list) else []

    async def claim_command(
        self,
        *,
        publication_id: str,
        command_id: str,
        runner_epoch: int,
        lease_owner: str,
        lease_ttl_ms: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Claim конкретной команды (CAS по статусу и runnerEpoch).

        None — команда уже занята другим раннером, устарела или сеть недоступна;
        это штатный проигрыш гонки, а не повод падать.
        """
        body: dict[str, Any] = {"runnerEpoch": int(runner_epoch), "leaseOwner": str(lease_owner)}
        if lease_ttl_ms is not None:
            body["leaseTtlMs"] = int(lease_ttl_ms)
        try:
            result = await self._request(
                "POST",
                f"{_RUNNER_PREFIX}/publications/{publication_id}/commands/{command_id}/claim",
                json_body=body,
            )
        except ControlPlaneError as error:
            log.warning("RC claim команды %s не удался (%s)", command_id, error.code)
            return None
        return result if isinstance(result, dict) else None

    async def submit_command_result(
        self,
        *,
        publication_id: str,
        command_id: str,
        status: str,
        result_summary: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ) -> bool:
        """Итоговый результат команды (source of truth статуса для владельца)."""
        body: dict[str, Any] = {"status": status}
        if result_summary is not None:
            body["resultSummary"] = result_summary
        if error_code is not None:
            body["errorCode"] = error_code
        try:
            await self._request(
                "POST",
                f"{_RUNNER_PREFIX}/publications/{publication_id}/commands/{command_id}/result",
                json_body=body,
            )
            return True
        except ControlPlaneError as error:
            log.warning("RC результат команды %s не доставлен (%s)", command_id, error.code)
            return False

    async def send_events(
        self, *, publication_id: str, events: list[dict[str, Any]]
    ) -> bool:
        """Пачка событий раннера (эфемерный прогресс, не источник истины статуса)."""
        try:
            result = await self._request(
                "POST",
                f"{_RUNNER_PREFIX}/publications/{publication_id}/events",
                json_body={"events": events},
            )
        except ControlPlaneError as error:
            log.warning("RC события не доставлены (%s)", error.code)
            return False
        return isinstance(result, dict)

    async def heartbeat(self, *, publication_id: str, state: str) -> bool:
        """Heartbeat публикации. Идентичность устройства — из access-токена."""
        try:
            await self._request(
                "POST",
                f"{_RUNNER_PREFIX}/publications/{publication_id}/heartbeat",
                json_body={"state": state},
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
