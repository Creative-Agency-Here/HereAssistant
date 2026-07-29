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

Сетевая логика сюда не кладётся: координатор лишь вызывает готовые куски ядра
(``ControlPlaneClient``, ``git_actions``, ``credential_store``) — сам протокол и
транспорт им не переписываются.
"""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import logging
import socket
import sqlite3
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional, TextIO

from chat_renderer import B, C, D, G, R, W, X, Y
from chat_sessions import Session
from core import project_config
from core.remote_control import config as rc_config
from core.remote_control import credential_store, events, git_actions, publications, receipts
from core.remote_control.control_plane_client import ControlPlaneClient
from core.remote_control.credential_store import CredentialStoreError, DeviceCredential

# Запуск одного промпта: принимает текст, возвращает признак успешного завершения.
# Запуск промпта возвращает (завершено, текст ответа): текст нужен предпросмотру
# состояния интеграции (его читает расширение VS Code), поэтому не теряем его.
RunPrompt = Callable[[str], Awaitable[tuple[bool, str]]]
PolicyLookup = Callable[[str], project_config.ProjectPolicy]

# Приоритет источника при равенстве момента принятия: локальный ввод выше.
_log = logging.getLogger("bridge.remote_control.coordinator")

_SOURCE_RANK = {"local": 0, "remote": 1}
_SOURCE_LABEL = {"local": "локально", "remote": "удалённо"}

# Типы Git-команд из фиксированного набора receipts.ALLOWED_COMMAND_TYPES,
# которые исполняются через готовый core/remote_control/git_actions.py.
_GIT_ACTION_TYPES = frozenset({"git_preflight", "git_commit", "git_push"})


def _hash_payload(payload: dict[str, Any]) -> str:
    """Детерминированный hash payload для идемпотентности receipt.

    Используется, только когда control-plane не прислал готовый payload_hash
    сам (например, в тестах). Реальный сервер обычно подписывает hash сам.
    """
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_control_client(
    *,
    credential_loader: Optional[Callable[[], Optional[DeviceCredential]]] = None,
) -> tuple[Optional[ControlPlaneClient], Optional[str]]:
    """Строит клиент control-plane, только если URL И credential заданы разом.

    Инвариант режима /rc: пустой ``RC_CONTROL_PLANE_URL`` или отсутствующий
    credential устройства — сеть выключена целиком. В этом случае
    ``ControlPlaneClient`` не создаётся вовсе (не просто «не используется») —
    вызвать его конструктор в обход этой функции неоткуда, поэтому без обоих
    условий сразу не может произойти ни одного сетевого обращения.
    """
    if not rc_config.configured():
        return None, None
    loader = credential_loader or (lambda: credential_store.default_store().load())
    credential = loader()
    if credential is None:
        return None, None
    return ControlPlaneClient(credential=credential), credential.device_id


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
        credential_store_factory: Callable[
            [], credential_store.CredentialStore
        ] = credential_store.default_store,
        secret_reader: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._session = session
        self._run_prompt = run_prompt
        self._output = output
        self._policy_lookup = policy_lookup
        self._client = control_client
        self._device_id = device_id or stable_device_id(session)
        self._device_name = device_name
        self._device_kind = device_kind
        # Хранилище device credential инжектируется для тестируемости; по
        # умолчанию — то же Keychain/файл-0600, что и у остального /rc.
        self._credential_store_factory = credential_store_factory
        # Чтение секрета скрытым полем (без эха и без истории readline).
        # getpass.getpass — единственный дефолт: он не проходит через
        # prompt_toolkit и не попадает в его InMemoryHistory.
        self._secret_reader = secret_reader or getpass.getpass
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

    # ---------- device credential ----------
    def connect_device(self) -> None:
        """Подключает устройство: credential вводится скрытым полем.

        Секрет никогда не приходит аргументом команды и не проходит через
        историю ввода терминала — только через ``getpass``-подобное скрытое
        поле, читаемое прямо с tty.
        """
        try:
            raw = self._secret_reader(
                "вставь выданный control-plane credential (ввод скрыт) › "
            )
        except (EOFError, KeyboardInterrupt):
            self._print(f"\n{D}подключение отменено{X}")
            return
        raw = raw.strip()
        if not raw:
            self._print(f"{D}пустой ввод — подключение отменено{X}")
            return
        try:
            credential = DeviceCredential.from_json(raw)
        except (CredentialStoreError, ValueError):
            self._print(f"{R}некорректный credential — не удалось разобрать{X}")
            return
        finally:
            # Сырая строка секрета не должна задерживаться в кадре дольше нужного.
            raw = ""
        try:
            self._credential_store_factory().save(credential)
        except CredentialStoreError as error:
            self._print(f"{R}не удалось сохранить credential: {error}{X}")
            return
        client, device_id = resolve_control_client(credential_loader=lambda: credential)
        if client is not None:
            self._client = client
            self._device_id = device_id or self._device_id
            self._print(
                f"{G}▸ устройство подключено{X} "
                f"{D}(control-plane активируется при следующей публикации /rc){X}"
            )
        else:
            self._print(
                f"{G}▸ credential сохранён{X} "
                f"{D}(RC_CONTROL_PLANE_URL не задан — сеть остаётся выключенной){X}"
            )

    def disconnect_device(self) -> None:
        """Удаляет credential устройства и немедленно останавливает сеть."""
        store = self._credential_store_factory()
        had_credential = store.load() is not None
        store.delete()
        if self._client is not None:
            self._stop_network()
            self._client = None
        if had_credential:
            self._print(f"{G}▸ устройство отключено — credential удалён{X}")
        else:
            self._print(f"{D}устройство не было подключено{X}")

    def device_status(self) -> None:
        """Показывает состояние device credential без раскрытия секрета."""
        credential = self._credential_store_factory().load()
        if credential is None:
            self._print(f"{D}устройство не подключено{X} {D}(/rc connect){X}")
            return
        scopes = ", ".join(credential.scopes) or "—"
        expiry = str(credential.expires_at) if credential.expires_at else "без срока"
        self._print(
            f"{B}устройство подключено{X}\n"
            f"  {D}device id{X}   {W}{credential.device_id}{X}\n"
            f"  {D}scopes{X}      {W}{scopes}{X}\n"
            f"  {D}истекает{X}    {W}{expiry}{X}"
        )

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
                self._emit_command_status(item.command_id, "running")
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
            self._emit_command_status(
                item.command_id, "succeeded" if completed else "failed"
            )
            if completed:
                # Сводка правок и вызовы инструментов — из meta провайдера,
                # которую _run_prompt уже записал в session.last_meta к этому
                # моменту. Собственного источника диффа/tool-call у координатора
                # нет — он берёт то же, что уже видит /diff в терминале.
                self._emit_diff_summary(item.command_id)
                self._emit_tool_calls(item.command_id)
        self._maybe_start()

    def _emit_command_status(
        self,
        command_id: Optional[str],
        state: str,
        *,
        commit_sha: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> None:
        """Ставит событие смены статуса команды в outbox (гейты — в ядре)."""
        policy = self._policy_lookup(self._session.cwd)
        publication = publications.get(self._key()) or {}
        events.emit_command_status(
            policy,
            command_id=command_id,
            state=state,
            publication_id=publication.get("id"),
            commit_sha=commit_sha,
            commit_message=commit_message,
        )

    def _emit_diff_summary(self, command_id: Optional[str]) -> None:
        """Сводка правок последнего запуска (только счётчики и пути, без содержимого)."""
        last_meta = self._session.last_meta
        raw_edits = last_meta.get("edits") if isinstance(last_meta, Mapping) else None
        edits = raw_edits if isinstance(raw_edits, list) else []
        if not edits:
            return
        files_changed = 0
        insertions = 0
        deletions = 0
        paths: list[str] = []
        for raw_edit in edits:
            edit = raw_edit if isinstance(raw_edit, Mapping) else {}
            files_changed += 1
            added = edit.get("added")
            removed = edit.get("removed")
            insertions += added if isinstance(added, int) and not isinstance(added, bool) else 0
            deletions += removed if isinstance(removed, int) and not isinstance(removed, bool) else 0
            path = edit.get("file")
            if path:
                paths.append(str(path))
        policy = self._policy_lookup(self._session.cwd)
        publication = publications.get(self._key()) or {}
        events.emit_diff_summary(
            policy,
            command_id=command_id,
            publication_id=publication.get("id"),
            files_changed=files_changed,
            insertions=insertions,
            deletions=deletions,
            paths=paths,
            project_root=self._session.cwd,
        )

    def _emit_tool_calls(self, command_id: Optional[str]) -> None:
        """События по инструментам последнего запуска — из session.last_meta.

        Пошаговый прогресс (``meta["steps"]``) сегодня репортит только парсер
        Claude Code; у провайдеров без него список пуст и событий не будет —
        придумывать источник для них координатор не должен.
        """
        last_meta = self._session.last_meta
        raw_steps = last_meta.get("steps") if isinstance(last_meta, Mapping) else None
        steps = raw_steps if isinstance(raw_steps, list) else []
        if not steps:
            return
        policy = self._policy_lookup(self._session.cwd)
        publication = publications.get(self._key()) or {}
        for raw_step in steps:
            step = raw_step if isinstance(raw_step, Mapping) else {}
            tool = step.get("name")
            if not tool:
                continue
            events.emit_tool_call(
                policy,
                command_id=command_id,
                publication_id=publication.get("id"),
                tool=str(tool),
                status=str(step.get("status") or "done"),
            )

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
        # команды — через HTTPS список + поштучный claim (источник истины),
        # WS лишь будит.
        client = self._client
        if client is None or not client.configured():
            return
        while not self._stop.is_set():
            try:
                await self._reconcile(client)
                publication = publications.get(self._key()) or {}
                remote_publication_id = publication.get("remote_public_id")
                if remote_publication_id:
                    await client.heartbeat(
                        publication_id=str(remote_publication_id),
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
        remote_publication_id = publication.get("remote_public_id")
        if not remote_publication_id:
            # Без серверного UUID публикации (POST publications ещё не
            # подтверждён control-plane) запрашивать publications/:id/* нечего —
            # тихо ждём следующего цикла, а не шлём заведомо невалидный путь.
            return
        last_sequence = int(publication.get("last_sequence") or 0)
        commands = await client.list_commands(
            publication_id=str(remote_publication_id), after_sequence=last_sequence
        )
        for command in commands:
            command_id = str(command.get("id") or "")
            if not command_id:
                continue
            claimed = await client.claim_command(
                publication_id=str(remote_publication_id),
                command_id=command_id,
                runner_epoch=int(command.get("runnerEpoch") or 1),
                lease_owner=self._device_id,
            )
            if claimed is not None:
                self._ingest_remote_command(claimed)

    def _ingest_remote_command(self, command: dict[str, Any]) -> None:
        """Диспетчер входящих команд control-plane по фиксированному типу.

        Единственные исполняемые типы — ``receipts.ALLOWED_COMMAND_TYPES``
        (``prompt``, ``stop``, ``approval_decision``, ``git_preflight``,
        ``git_commit``, ``git_push``); всё остальное отклоняется fail-closed
        внутри ``receipts.claim`` (``unknown_command_type``), сюда шелл или
        произвольная команда попасть не может в принципе.
        """
        command_id = str(command.get("id") or "")
        if not command_id:
            return
        sequence = int(command.get("sequence") or 0)
        command_type = str(command.get("commandType") or command.get("type") or "prompt")
        payload = command.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        publications.advance_sequence(self._key(), sequence)

        if command_type == "prompt":
            self._ingest_prompt_command(command_id, sequence, payload, command.get("payload_hash"))
            return

        payload_hash = str(command.get("payload_hash") or _hash_payload(payload))
        if command_type == "stop":
            self._ingest_stop_command(command_id, sequence, payload_hash)
        elif command_type == "approval_decision":
            self._ingest_approval_decision(command_id, sequence, payload_hash)
        elif command_type in _GIT_ACTION_TYPES:
            self._ingest_git_action(command_id, sequence, command_type, payload, payload_hash)
        else:
            # Неизвестный тип: receipts.claim отклонит его до какого-либо
            # исполнения (unknown_command_type), receipt не создаётся вовсе.
            receipts.claim(
                command_id, sequence=sequence, command_type=command_type, payload_hash=payload_hash
            )

    def _ingest_prompt_command(
        self,
        command_id: str,
        sequence: int,
        payload: dict[str, Any],
        raw_payload_hash: Optional[str],
    ) -> None:
        prompt = str(payload.get("prompt") or "")
        payload_hash = str(raw_payload_hash or hashlib.sha256(prompt.encode("utf-8")).hexdigest())
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

    def _ingest_stop_command(self, command_id: str, sequence: int, payload_hash: str) -> None:
        """``stop`` — идемпотентная отмена текущего запуска, очередь не трогает."""
        claim = receipts.claim(
            command_id, sequence=sequence, command_type="stop", payload_hash=payload_hash
        )
        if not claim.should_execute:
            return
        receipts.mark_running(command_id)
        self.stop_run()
        receipts.finish(command_id, state="succeeded")
        if self._active:
            self._emit_command_status(command_id, "succeeded")

    def _ingest_approval_decision(
        self, command_id: str, sequence: int, payload_hash: str
    ) -> None:
        """``approval_decision`` без живого канала подтверждения — fail closed.

        Ни один провайдер здесь не поддерживает промежуточное подтверждение
        инструмента: Codex запускается с ``approval_policy=never``, Claude —
        неинтерактивно (см. ``chat_sessions.Session.permission_mode``). Живого
        approval-канала на устройстве физически нет, поэтому команда всегда
        отклоняется явным отказом, а не автоодобрением — иначе это значило бы
        согласие на действие, которое никто не видел.
        """
        claim = receipts.claim(
            command_id,
            sequence=sequence,
            command_type="approval_decision",
            payload_hash=payload_hash,
        )
        if not claim.should_execute:
            return
        receipts.mark_running(command_id)
        receipts.finish(command_id, state="rejected")
        if self._active:
            self._emit_command_status(command_id, "rejected")

    def _ingest_git_action(
        self,
        command_id: str,
        sequence: int,
        action: str,
        payload: dict[str, Any],
        payload_hash: str,
    ) -> None:
        claim = receipts.claim(
            command_id, sequence=sequence, command_type=action, payload_hash=payload_hash
        )
        if not claim.should_execute:
            return
        try:
            asyncio.create_task(self._run_git_action(command_id, action, payload))
        except RuntimeError:
            pass  # нет работающего цикла — как и для очереди промптов

    async def _run_git_action(
        self, command_id: str, action: str, payload: dict[str, Any]
    ) -> None:
        """Исполняет typed-intent git_actions; сам git_actions несёт privacy-гейт."""
        receipts.mark_running(command_id)
        if self._active:
            self._emit_command_status(command_id, "running")
        policy = self._policy_lookup(self._session.cwd)
        try:
            if action == "git_preflight":
                result = await git_actions.git_preflight(
                    policy,
                    user_id=self._session.user_id,
                    root=self._session.cwd,
                    remote=str(payload.get("remote") or "origin"),
                )
            elif action == "git_commit":
                raw_paths = payload.get("paths") or []
                paths = [str(p) for p in raw_paths] if isinstance(raw_paths, list) else []
                result = await git_actions.git_commit(
                    policy,
                    user_id=self._session.user_id,
                    root=self._session.cwd,
                    paths=paths,
                    message=str(payload.get("message") or ""),
                )
            else:  # action == "git_push" (третий и последний тип в _GIT_ACTION_TYPES)
                result = await git_actions.git_push(
                    policy,
                    user_id=self._session.user_id,
                    root=self._session.cwd,
                    remote=str(payload.get("remote") or "origin"),
                )
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, asyncio.TimeoutError):
            _log.exception("сбой удалённого git-действия /rc: %s", action)
            receipts.finish(command_id, state="failed")
            if self._active:
                self._emit_command_status(command_id, "failed")
            return

        state = "succeeded" if result.ok else "failed"
        result_hash = _hash_payload(result.payload())
        receipts.finish(command_id, state=state, result_hash=result_hash)
        if not self._active:
            return
        commit_sha = result.data.get("sha") if action == "git_commit" and result.ok else None
        commit_message = payload.get("message") if commit_sha else None
        self._emit_command_status(
            command_id,
            state,
            commit_sha=str(commit_sha) if commit_sha else None,
            commit_message=str(commit_message) if commit_message else None,
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
        elif arg == "connect":
            self.connect_device()
        elif arg == "disconnect":
            self.disconnect_device()
        elif arg == "device":
            self.device_status()
        else:
            self._print(
                f"{R}неизвестная команда /rc {argument}{X} — "
                f"{C}/rc{X}, {C}/rc status{X}, {C}/rc stop{X}, {C}/rc off{X}, "
                f"{C}/rc connect{X}, {C}/rc device{X}, {C}/rc disconnect{X}"
            )

    def _print(self, text: str) -> None:
        self._output.write(text + "\n")
