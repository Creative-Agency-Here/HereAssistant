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

AccountLookup = Callable[[str], AccountRecord | None]
UsersLookup = Callable[[], Sequence[UserRecord]]
WorkspaceLookup = Callable[[int], str]
ResumeLookup = Callable[[Session], list[ResumableSession]]


class CommandRouter:
    def __init__(
        self,
        *,
        account_by_label: AccountLookup,
        users: UsersLookup,
        default_cwd: WorkspaceLookup,
        resumable: ResumeLookup,
        output: TextIO = sys.stdout,
        read: Callable[[str], str] = input,
        system: Callable[[str], int] = os.system,
    ) -> None:
        self.account_by_label = account_by_label
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
        elif command == "/model":
            self._model(session, argument)
        elif command == "/account":
            self._account(session, argument)
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
  {C}/user{X} [@имя|id]    кто работает: workspace и сессии пишутся на него
  {C}/cwd{X} [путь]        показать/сменить рабочую папку
  {C}/new{X}               начать новую сессию (забыть контекст)
  {C}/resume{X}            выбрать и продолжить прошлую сессию проекта
  {C}/status{X}            аккаунт, модель, папка, id сессии
  {C}/diff{X}              диффы правок последнего ответа
  {C}/clear{X}             очистить экран
  {C}/exit{X} (или Ctrl+D) выход
{D}Просто пиши текст — это уйдёт агенту. Отправляется по Enter.{X}"""
        )

    def status(self, session: Session) -> None:
        self._print(
            f"  {D}кто    {X}  {W}{session.user_name or session.user_id}{X}"
            f"  {D}— от его имени: workspace, сессии и архив{X}"
        )
        self._print(f"  {D}аккаунт{X}  {W}{session.label}{X}  {D}({session.provider}){X}")
        self._print(f"  {D}модель {X}  {W}{session.model or '—'}{X}")
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
        account = self.account_by_label(argument)
        if account and account["enabled"]:
            session.account = account
            session.model = account["default_model"]
            session.session_id = None
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
        session.user_id = int(user["telegram_id"])
        session.user_name = user_display(user)
        session.cwd = self.default_cwd(session.user_id)
        session.session_id = None
        self._print(f"{G}▸ теперь работает {session.user_name} (workspace и сессия — его){X}")

    def _print(self, text: str) -> None:
        self.output.write(text + "\n")


def pretty_path(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home) else path
