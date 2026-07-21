"""Slash-command router терминального чата с явными зависимостями."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from chat_identity import UserRecord, find_user, user_display
from chat_renderer import B, C, D, G, R, W, X
from chat_sessions import AccountRecord, ResumableSession, Session
from core.workspace_status import workspace_overview

AccountsLookup = Callable[[int], Sequence[AccountRecord]]
UsersLookup = Callable[[], Sequence[UserRecord]]
WorkspaceLookup = Callable[[int], str]
ResumeLookup = Callable[[Session], list[ResumableSession]]

COMMAND_SPECS: tuple[tuple[str, str], ...] = (
    ("/help", "справка по командам"),
    ("/status", "сессия, задачи, Git, диск и деплой"),
    ("/tasks", "задачи HereCRM выбранного проекта"),
    ("/model", "показать или сменить модель"),
    ("/account", "показать или сменить AI-аккаунт"),
    ("/permissions", "режим песочницы Codex"),
    ("/user", "сменить пользователя workspace"),
    ("/cwd", "показать или сменить рабочую папку"),
    ("/new", "начать сессию с чистого контекста"),
    ("/resume", "продолжить прошлую сессию"),
    ("/diff", "правки последнего ответа"),
    ("/clear", "очистить экран"),
    ("/exit", "закрыть терминальный чат"),
)

PERMISSION_MODES = {
    "account": "профиль аккаунта Codex",
    "read-only": "только чтение",
    "workspace": "запись только в рабочем пространстве",
}


def git_state_label(snapshot: Mapping[str, object]) -> str:
    labels = {
        "changes": f"{snapshot.get('dirty', 0)} изменений не зафиксировано",
        "diverged": f"расхождение: ↑{snapshot.get('ahead', 0)} ↓{snapshot.get('behind', 0)}",
        "push_needed": f"нужно отправить {snapshot.get('ahead', 0)} коммитов",
        "pull_needed": f"нужно получить {snapshot.get('behind', 0)} коммитов",
        "synced": "синхронизировано",
    }
    return labels.get(str(snapshot.get("state")), "состояние неизвестно")


def deployment_label(state: object) -> str:
    return {
        "deployed": "задеплоено",
        "partial": "частично",
        "pending": "ожидает деплоя",
        "unknown": "нет подтверждения",
    }.get(str(state), "нет подтверждения")


class CommandRouter:
    def __init__(
        self,
        *,
        accounts: AccountsLookup,
        users: UsersLookup,
        default_cwd: WorkspaceLookup,
        resumable: ResumeLookup,
        output: TextIO = sys.stdout,
        read: Callable[[str], str] = input,
        system: Callable[[str], int] = os.system,
    ) -> None:
        self.accounts = accounts
        self.users = users
        self.default_cwd = default_cwd
        self.resumable = resumable
        self.output = output
        self.read = read
        self.system = system

    def handle(self, session: Session, line: str) -> bool:
        parts = line.strip().split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        if command in ("/exit", "/quit", "/q"):
            return False
        if command == "/help":
            self.help()
        elif command == "/status":
            self.status(session)
        elif command == "/tasks":
            self.tasks(session)
        elif command == "/model":
            self._model(session, argument)
        elif command == "/account":
            self._account(session, argument)
        elif command == "/permissions":
            self._permissions(session, argument)
        elif command == "/cwd":
            self._cwd(session, argument)
        elif command == "/user":
            self._user(session, argument)
        elif command == "/new":
            session.session_id = None
            self._print(f"{G}▸ новая сессия — контекст забыт{X}")
        elif command == "/resume":
            self.resume(session)
        elif command == "/diff":
            self.diff(session)
        elif command == "/clear":
            self.system("cls" if os.name == "nt" else "clear")
        else:
            self._print(f"{R}неизвестная команда {command}{X} — /help")
        return True

    def help(self) -> None:
        self._print(
            f"""{B}Команды:{X}
  {C}/help{X}              эта справка
  {C}/model{X} [имя]       показать/сменить модель
  {C}/account{X} [label]   показать/сменить аккаунт (сбрасывает сессию)
  {C}/permissions{X}       режим песочницы Codex
  {C}/user{X} [@имя|id]    кто работает: workspace и сессии пишутся на него
  {C}/cwd{X} [путь]        показать/сменить рабочую папку
  {C}/new{X}               начать новую сессию (забыть контекст)
  {C}/resume{X}            выбрать и продолжить прошлую сессию проекта
  {C}/status{X}            сессия, задачи, Git, диск и деплой
  {C}/tasks{X}             задачи HereCRM выбранного проекта
  {C}/diff{X}              диффы правок последнего ответа
  {C}/clear{X}             очистить экран
  {C}/exit{X} (или Ctrl+D) выход
{D}Просто пиши текст — это уйдёт агенту. Отправляется по Enter.{X}"""
        )

    def _permissions(self, session: Session, argument: str) -> None:
        if session.provider != "codex":
            self._print(
                f"{D}/permissions сейчас управляет только Codex. "
                f"Для {session.provider} действует безопасный профиль провайдера.{X}"
            )
            return
        mode = argument.lower().replace("_", "-")
        aliases = {"readonly": "read-only", "default": "account"}
        mode = aliases.get(mode, mode)
        if mode:
            if mode not in PERMISSION_MODES:
                self._print(
                    f"{R}неизвестный режим '{argument}'{X} — account, read-only или workspace"
                )
                return
            session.permission_mode = mode
            self._print(f"{G}▸ разрешения: {PERMISSION_MODES[mode]}{X}")
            return
        self._print(f"{B}Разрешения Codex:{X}")
        for name, label in PERMISSION_MODES.items():
            current = f" {G}← текущий{X}" if name == session.permission_mode else ""
            self._print(f"  {C}/permissions {name}{X}  {D}— {label}{X}{current}")
        self._print(
            f"{D}HereAssistant использует неинтерактивный codex exec: запрещённая "
            f"операция завершится ошибкой без окна «разрешить один раз».{X}"
        )

    def status(self, session: Session) -> None:
        overview = workspace_overview(session.user_id, session.cwd)
        self._print(
            f"  {D}кто    {X}  {W}{session.user_name or session.user_id}{X}"
            f"  {D}— от его имени: workspace, сессии и архив{X}"
        )
        self._print(f"  {D}аккаунт{X}  {W}{session.label}{X}  {D}({session.provider}){X}")
        self._print(f"  {D}модель {X}  {W}{session.model or '—'}{X}")
        if session.provider == "codex":
            self._print(
                f"  {D}доступ {X}  {W}{PERMISSION_MODES[session.permission_mode]}{X}"
                f"  {D}· /permissions{X}"
            )
        self._print(f"  {D}проект {X}  {W}{pretty_path(session.cwd)}{X}")
        if session.session_id:
            self._print(
                f"  {D}сессия {X}  {W}{session.session_id[:8]}{X}"
                f"  {D}— контекст продолжается · /new — с чистого листа{X}"
            )
        else:
            self._print(
                f"  {D}сессия {X}  {W}новая{X}  {D}— контекст пуст · /resume — вернуть прошлую{X}"
            )
        tasks = overview["tasks"]
        git = overview["git"]
        current = git["current"]
        self._print(
            f"  {D}задачи {X}  {W}{tasks['open']} в работе{X}  "
            f"{D}({'связано с HereCRM' if tasks['linked'] else 'локальный проект'}){X}"
        )
        self._print(
            f"  {D}Git    {X}  {W}{git['connections']} подключений · "
            f"{git['repositories']} доступно · {overview['repositoriesOnDisk']} на диске{X}"
        )
        if current.get("available"):
            self._print(
                f"  {D}ветка  {X}  {W}{current['branch']}{X}  {D}· {git_state_label(current)}{X}"
            )
        self._print(
            f"  {D}диск   {X}  {W}{overview['disk']['freeLabel']} свободно{X}  "
            f"{D}· деплой: {deployment_label(overview['deployment']['state'])}{X}"
        )

    def tasks(self, session: Session) -> None:
        tasks = workspace_overview(session.user_id, session.cwd)["tasks"]
        if not tasks["linked"]:
            self._print(
                f"{D}Проект не связан с HereCRM. Добавь crm_project_id в "
                f".hereassistant/project.yml и включи sync.{X}"
            )
            return
        self._print(f"{B}HereCRM · {tasks['open']} в работе{X}")
        if not tasks["titles"]:
            self._print(f"  {G}✓ открытых задач нет{X}")
        for index, title in enumerate(tasks["titles"], 1):
            self._print(f"  {B}{index}.{X} {title}")

    def resume(self, session: Session) -> None:
        items = self.resumable(session)
        if not items:
            self._print(
                f"{D}Прошлых сессий для этого проекта не найдено "
                f"(или провайдер не поддерживает).{X}"
            )
            return
        self._print(f"{B}Прошлые сессии этого проекта:{X}")
        for index, item in enumerate(items, 1):
            ago = time.strftime("%d.%m %H:%M", time.localtime(item.mtime))
            current = f" {G}← текущая{X}" if item.session_id == session.session_id else ""
            self._print(
                f"  {B}{index}{X}. {item.title}  {D}{ago} · {item.session_id[:8]}{X}{current}"
            )
        raw = self.read(f"{D}номер (Enter — отмена) › {X}").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            session.session_id = items[int(raw) - 1].session_id
            self._print(f"{G}▸ продолжаю сессию {session.session_id[:8]}{X}")

    def diff(self, session: Session) -> None:
        raw_edits = session.last_meta.get("edits") or []
        edits = raw_edits if isinstance(raw_edits, list) else []
        if not edits:
            self._print(f"{D}В последнем ответе правок файлов не было.{X}")
            return
        for raw_edit in edits:
            edit = raw_edit if isinstance(raw_edit, Mapping) else {}
            self._print(
                f"\n{B}{edit.get('file', '?')}{X}  {G}+{edit.get('added', 0)}{X} "
                f"{R}−{edit.get('removed', 0)}{X}"
            )
            old = str(edit.get("old") or "").splitlines()
            new = str(edit.get("new") or "").splitlines()
            for line in old[:40]:
                self._print(f"{R}- {line}{X}")
            for line in new[:40]:
                self._print(f"{G}+ {line}{X}")
            if len(old) > 40 or len(new) > 40:
                self._print(f"{D}  …(обрезано, полностью — в вебапе правок){X}")

    def _model(self, session: Session, argument: str) -> None:
        if argument:
            session.model = argument
            self._print(f"{G}▸ модель: {argument}{X}")
        else:
            self._print(f"  модель: {session.model or '—'}  {D}(/model <имя> — сменить){X}")

    def _account(self, session: Session, argument: str) -> None:
        if not argument:
            self._print(
                f"  аккаунт: {session.label} {D}({session.provider}){X}  {D}(/account <label>){X}"
            )
            return
        account = next(
            (item for item in self.accounts(session.user_id) if item["label"] == argument),
            None,
        )
        if account:
            session.account = account
            session.model = account["default_model"]
            session.session_id = None
            session.permission_mode = "account"
            self._print(f"{G}▸ аккаунт: {argument} · {account['provider']} (сессия сброшена){X}")
        else:
            self._print(f"{R}аккаунт '{argument}' не найден/выключен{X}")

    def _cwd(self, session: Session, argument: str) -> None:
        if not argument:
            self._print(f"  проект: {pretty_path(session.cwd)}  {D}(/cwd <путь> — сменить){X}")
            return
        path = Path(os.path.expanduser(argument))
        if path.is_dir():
            session.cwd = str(path.resolve())
            session.session_id = None
            self._print(f"{G}▸ проект: {pretty_path(session.cwd)} (сессия сброшена){X}")
        else:
            self._print(f"{R}нет такой папки: {argument}{X}")

    def _user(self, session: Session, argument: str) -> None:
        if not argument:
            self._print(
                f"  кто: {session.user_name or session.user_id}  "
                f"{D}(/user <id|@username> — сменить){X}"
            )
            return
        user = find_user(self.users(), argument)
        if user is None:
            self._print(f"{R}пользователь '{argument}' не найден{X}")
            return
        user_id = int(user["telegram_id"])
        accounts = self.accounts(user_id)
        if not accounts:
            self._print(f"{R}У пользователя {user_display(user)} нет доступных аккаунтов.{X}")
            return
        session.user_id = user_id
        session.user_name = user_display(user)
        session.account = accounts[0]
        session.model = session.account["default_model"]
        session.cwd = self.default_cwd(session.user_id)
        session.session_id = None
        session.permission_mode = "account"
        self._print(f"{G}▸ теперь работает {session.user_name} (workspace и сессия — его){X}")

    def _print(self, text: str) -> None:
        self.output.write(text + "\n")


def pretty_path(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home) else path
