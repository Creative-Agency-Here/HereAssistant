"""Координатор сессии /rc: единый замок исполнения, FIFO-очередь, публикации.

Связывает готовое ядро ``core/remote_control`` (транспорт, receipts, privacy) с
терминальным чатом. Отвечает за арбитраж локального и удалённого ввода (ответ 4
плана RC_REMOTE_CONTROL_PLAN.md, этап P3):

* ровно один провайдерский запуск на сессию — сериализует ``asyncio.Lock``;
* текущий запуск не вытесняется: пришедший ввод встаёт в FIFO-очередь;
* порядок очереди — по моменту локального принятия, при равенстве приоритет у
  локального ввода;
* удалённая сторона не меняет провайдера/аккаунт/модель/cwd/режим разрешений —
  удалённый ввод это только prompt, идущий тем же путём, что и локальный.

Сетевая логика сюда не кладётся: координатор лишь вызывает ядро.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional, TextIO

from chat_renderer import B, C, D, G, R, W, X, Y
from chat_sessions import Session
from core import project_config
from core.remote_control import config as rc_config
from core.remote_control import publications, receipts

# Запуск одного промпта: принимает текст, возвращает признак успешного завершения.
# Запуск промпта возвращает (завершено, текст ответа): текст нужен предпросмотру
# состояния интеграции (его читает расширение VS Code), поэтому не теряем его.
RunPrompt = Callable[[str], Awaitable[tuple[bool, str]]]
PolicyLookup = Callable[[str], project_config.ProjectPolicy]

# Приоритет источника при равенстве момента принятия: локальный ввод выше.
_log = logging.getLogger("bridge.remote_control.coordinator")

_SOURCE_RANK = {"local": 0, "remote": 1}
_SOURCE_LABEL = {"local": "локально", "remote": "удалённо"}


@dataclass
class QueuedItem:
    """Единица ввода в очереди исполнения."""

    source: str  # "local" | "remote"
    prompt: str
    accepted_seq: int  # монотонный момент локального принятия
    command_id: Optional[str] = None  # id удалённой команды (для receipt)
    done: Optional["asyncio.Future[tuple[bool, str]]"] = None  # (завершено, ответ)


@dataclass
class SubmitResult:
    """Итог принятия ввода: запущен сейчас или встал в очередь."""

    started_now: bool
    position: int  # 0 при немедленном запуске, иначе номер в очереди (с 1)
    item: QueuedItem


def stable_device_id(session: Session) -> str:
    """Стабильный id устройства: пользователь + машина, без секретов."""
    raw = f"{session.user_id}:{socket.gethostname()}".encode("utf-8")
    return "dev_" + hashlib.sha256(raw).hexdigest()[:16]


class RemoteControlCoordinator:
    """Session actor: единственный писатель провайдер-сессии."""

    def __init__(
        self,
        session: Session,
        *,
        run_prompt: RunPrompt,
        output: TextIO = sys.stdout,
        policy_lookup: PolicyLookup = project_config.policy_for,
        control_client: Any = None,
        device_id: Optional[str] = None,
        device_name: str = "Это устройство",
        device_kind: str = "desktop",
    ) -> None:
        self._session = session
        self._run_prompt = run_prompt
        self._output = output
        self._policy_lookup = policy_lookup
        self._client = control_client
        self._device_id = device_id or stable_device_id(session)
        self._device_name = device_name
        self._device_kind = device_kind
        # Единый замок исполнения: держится ровно на время одного запуска.
        self._exec_lock = asyncio.Lock()
        self._queue: list[QueuedItem] = []
        self._accept_seq = 0
        self._running_item: Optional[QueuedItem] = None
        self._running_task: Optional["asyncio.Task[None]"] = None
        self._net_task: Optional["asyncio.Task[None]"] = None
        self._stop = asyncio.Event()
        self._active = False

    # ---------- состояние ----------
    def _key(self) -> str:
        return f"chat:{self._session.user_id}:{self._session.crm_conversation_id}"

    def is_active(self) -> bool:
        """Активна ли публикация (для гейтов смены сессии в роутере)."""
        return self._active

    def queue_snapshot(self) -> list[QueuedItem]:
        return list(self._ordered_queue())

    # ---------- публикации ----------
    def publish(self) -> bool:
        """Публикует сессию, если политика разрешает presence (default deny)."""
        if self._active:
            self._print(f"{D}публикация уже активна — /rc status{X}")
            return True
        policy = self._policy_lookup(self._session.cwd)
        publication = publications.publish(
            self._key(), policy=policy, device_id=self._device_id
        )
        if publication is None:
            self._print(
                f"{R}/rc запрещён политикой проекта.{X} "
                f"{D}Нужен явный remote_control.enabled в .hereassistant/project.yml "
                f"(для private — ещё и allow_presence_in_private).{X}"
            )
            return False
        self._active = True
        self._session.rc_publication = self._key()
        self._start_network()
        capabilities = publications.compile_capabilities(policy)
        allowed = [name for name, on in capabilities.items() if on] or ["только presence"]
        privacy = "crm" if policy.mode == "crm" else "private"
        self._print(
            f"{G}▸ сессия опубликована{X}\n"
            f"  {D}устройство{X}  {W}{self._device_name}{X} {D}({self._device_kind}){X}\n"
            f"  {D}приватность{X} {W}{privacy}{X}  {D}· TTL {policy.rc_ttl_minutes} мин{X}\n"
            f"  {D}наружу уходит{X} {W}{', '.join(allowed)}{X}\n"
            f"  {D}команды{X}     {C}/rc status{X} · {C}/rc stop{X} · {C}/rc off{X}"
        )
        return True

    def off(self) -> None:
        """Немедленно снимает публикацию и очищает очередь."""
        if not self._active:
            self._print(f"{D}публикации нет — снимать нечего{X}")
            return
        publications.close(self._key())
        self._active = False
        self._session.rc_publication = None
        cleared = len(self._queue)
        self._queue.clear()
        self._stop_network()
        tail = f", очередь очищена ({cleared})" if cleared else ""
        self._print(f"{G}▸ публикация снята{X}{D}{tail}{X}")

    def shutdown(self) -> None:
        """Гарантированное снятие публикации (зовётся из finally чата)."""
        self._stop_network()
        if self._active:
            try:
                publications.close(self._key())
            except (sqlite3.Error, OSError) as error:
                # Выход из чата не должен падать из-за занятой БД.
                _log.warning("не удалось закрыть публикацию /rc: %s", error)
        self._active = False
        self._session.rc_publication = None
        self._queue.clear()

    def status(self) -> None:
        if not self._active:
            self._print(f"{D}публикации нет — {C}/rc{X}{D}, чтобы опубликовать{X}")
            return
        publication = publications.get(self._key()) or {}
        state = publication.get("state", "published_idle")
        privacy = publication.get("privacy_mode", "private")
        if self._running_item is not None:
            running = f"{_SOURCE_LABEL[self._running_item.source]}, выполняется"
        else:
            running = "ожидает ввода"
        self._print(f"{B}/rc · {state}{X}")
        self._print(f"  {D}приватность{X} {W}{privacy}{X}  {D}· устройство {self._device_name}{X}")
        self._print(f"  {D}запуск{X}      {W}{running}{X}")
        queued = self._ordered_queue()
        if not queued:
            self._print(f"  {D}очередь{X}     {G}пуста{X}")
        for index, item in enumerate(queued, 1):
            preview = item.prompt.replace("\n", " ")[:60]
            self._print(
                f"  {D}очередь {index}{X}   {_SOURCE_LABEL[item.source]} · {preview}"
            )

    def stop_run(self) -> None:
        """Останавливает текущий запуск (очередь не трогает)."""
        if self._running_task is not None:
            self._running_task.cancel()
            self._print(f"{Y}⏹ останавливаю текущий запуск{X}")
        else:
            self._print(f"{D}сейчас ничего не выполняется{X}")

    # ---------- приём ввода ----------
    def submit_local(self, prompt: str) -> SubmitResult:
        """Локальный ввод из терминала."""
        return self._enqueue("local", prompt, command_id=None)

    def submit_remote(
        self,
        prompt: str,
        *,
        command_id: str,
        sequence: int,
        payload_hash: str,
    ) -> Optional[SubmitResult]:
        """Удалённый ввод: идемпотентно через receipts, затем в общую очередь.

        Возвращает None, если команда отклонена (дубль/подмена/неизвестный тип).
        """
        claim = receipts.claim(
            command_id, sequence=sequence, command_type="prompt", payload_hash=payload_hash
        )
        if not claim.should_execute:
            return None
        return self._enqueue("remote", prompt, command_id=command_id)

    def _enqueue(self, source: str, prompt: str, *, command_id: Optional[str]) -> SubmitResult:
        self._accept_seq += 1
        done = asyncio.get_running_loop().create_future()
        item = QueuedItem(
            source=source,
            prompt=prompt,
            accepted_seq=self._accept_seq,
            command_id=command_id,
            done=done,
        )
        # Запуск сейчас возможен, только если замок исполнения свободен. Это и
        # есть гарантия «ровно один запуск»: при занятом замке ввод идёт в
        # очередь, второй запуск не стартует ни локально, ни удалённо.
        will_start_now = not self._exec_lock.locked()
        self._queue.append(item)
        self._maybe_start()
        if will_start_now:
            return SubmitResult(started_now=True, position=0, item=item)
        position = self._position_of(item)
        return SubmitResult(started_now=False, position=position, item=item)

    def _position_of(self, item: QueuedItem) -> int:
        for index, queued in enumerate(self._ordered_queue(), 1):
            if queued is item:
                return index
        return len(self._queue) + 1

    def _ordered_queue(self) -> list[QueuedItem]:
        # FIFO по моменту принятия; при равенстве — приоритет локального ввода.
        return sorted(
            self._queue,
            key=lambda it: (it.accepted_seq, _SOURCE_RANK.get(it.source, 1)),
        )

    def _pop_next(self) -> Optional[QueuedItem]:
        ordered = self._ordered_queue()
        if not ordered:
            return None
        head = ordered[0]
        self._queue.remove(head)
        return head

    def _maybe_start(self) -> None:
        # Новый запуск не стартует, пока занят замок исполнения.
        if self._exec_lock.locked():
            return
        if not self._queue:
            return
        try:
            asyncio.create_task(self._run_next())
        except RuntimeError:
            pass  # нет работающего цикла — запуск отложен (вне REPL)

    async def _run_next(self) -> None:
        async with self._exec_lock:
            item = self._pop_next()
            if item is None:
                return
            self._running_item = item
            self._running_task = asyncio.current_task()
            if item.command_id:
                receipts.mark_running(item.command_id)
            if self._active:
                publications.set_state(self._key(), "running")
            completed = False
            answer = ""
            try:
                completed, answer = await self._run_prompt(item.prompt)
            except asyncio.CancelledError:
                completed = False
                raise
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, asyncio.TimeoutError):
                # Сбой провайдера не роняет очередь, но и не остаётся немым:
                # молчаливое проглатывание прятало даже ошибку контракта.
                _log.exception("сбой запуска промпта из очереди /rc")
                completed = False
            finally:
                self._running_item = None
                self._running_task = None
                if item.command_id:
                    receipts.finish(
                        item.command_id,
                        state="succeeded" if completed else "failed",
                    )
                if item.done is not None and not item.done.done():
                    item.done.set_result((completed, answer))
        if self._active:
            publications.set_state(self._key(), "published_idle")
        self._maybe_start()

    # ---------- сеть (best effort, инертна без конфигурации) ----------
    def _start_network(self) -> None:
        if self._net_task is not None:
            return
        self._stop.clear()
        try:
            self._net_task = asyncio.create_task(self._network_loop())
        except RuntimeError:
            self._net_task = None

    def _stop_network(self) -> None:
        self._stop.set()
        if self._net_task is not None:
            self._net_task.cancel()
            self._net_task = None

    async def _network_loop(self) -> None:
        # Reconcile + heartbeat. Работает только при настроенном control-plane;
        # команды — через HTTPS claim (источник истины), WS лишь будит.
        client = self._client
        if client is None or not client.configured():
            return
        while not self._stop.is_set():
            try:
                await self._reconcile(client)
                publication = publications.get(self._key()) or {}
                await client.heartbeat(
                    device_id=self._device_id,
                    publication_id=str(
                        publication.get("remote_public_id") or self._key()
                    ),
                    state=str(publication.get("state") or "published_idle"),
                )
                publications.record_heartbeat(self._key())
            except (OSError, RuntimeError, ValueError, sqlite3.Error, asyncio.TimeoutError) as error:
                # Недоступный control-plane не должен ронять локальную сессию.
                _log.debug("heartbeat /rc не доставлен: %s", error)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=rc_config.HEARTBEAT_INTERVAL_SEC
                )
            except asyncio.TimeoutError:
                pass

    async def _reconcile(self, client: Any) -> None:
        publication = publications.get(self._key()) or {}
        last_sequence = int(publication.get("last_sequence") or 0)
        commands = await client.claim_pending(
            device_id=self._device_id, last_sequence=last_sequence
        )
        for command in commands:
            self._ingest_remote_command(command)

    def _ingest_remote_command(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id") or "")
        sequence = int(command.get("sequence") or 0)
        payload = command.get("payload") or {}
        prompt = str(payload.get("prompt") or "")
        payload_hash = str(
            command.get("payload_hash")
            or hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        )
        if not command_id:
            return
        publications.advance_sequence(self._key(), sequence)
        policy = self._policy_lookup(self._session.cwd)
        if not project_config.can_receive_remote_prompts(policy):
            # Приватный проект не исполняет удалённый prompt — fail closed.
            receipts.claim(
                command_id, sequence=sequence, command_type="prompt", payload_hash=payload_hash
            )
            receipts.finish(command_id, state="rejected")
            return
        self.submit_remote(
            prompt, command_id=command_id, sequence=sequence, payload_hash=payload_hash
        )

    # ---------- служебное ----------
    def handle_command(self, argument: str) -> None:
        """Диспетчер /rc (роутер зовёт сюда, сетевой логики там нет)."""
        arg = argument.strip().lower()
        if arg in ("", "on", "publish"):
            self.publish()
        elif arg in ("off", "close"):
            self.off()
        elif arg in ("status", "st"):
            self.status()
        elif arg == "stop":
            self.stop_run()
        else:
            self._print(
                f"{R}неизвестная команда /rc {argument}{X} — "
                f"{C}/rc{X}, {C}/rc status{X}, {C}/rc stop{X}, {C}/rc off{X}"
            )

    def _print(self, text: str) -> None:
        self._output.write(text + "\n")
