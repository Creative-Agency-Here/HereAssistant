#!/usr/bin/env python3
"""HereAssistant CLI — интерактивный терминальный чат с CLI-агентом.

То же, что бот в Telegram, но прямо в консоли: окно ввода, полный живой вывод
(рассуждения модели, вызовы инструментов и их результаты, диффы правок — без
лимита Telegram), продолжение прошлых сессий (/resume), настройки слэш-командами.
Работает поверх тех же подписок-аккаунтов HereAssistant (Claude / Codex / Gemini),
так что квота и вход — общие с ботом.

Запуск:  python chat.py                 — выбрать пользователя и аккаунт интерактивно
         python chat.py -a <label>      — сразу на аккаунте
         python chat.py -u <id|@имя>    — от имени конкретного пользователя
                                          (workspace и сессии пишутся на него)
Внутри:  /help — список команд.
"""

# Аннотации — ленивыми строками (PEP 563): в сигнатурах есть `str | None` /
# `Path | None`, а на Python 3.9 (частый системный на macOS) оператор `|` для
# типов падает при импорте. С этим импортом синтаксис не вычисляется — чат
# запускается на 3.9+ без правок аннотаций.
from __future__ import annotations

import asyncio
import os
import random
import sys
import time

import providers
from chat_commands import CommandRouter
from chat_identity import find_user as _find_user
from chat_identity import user_display as _user_display
from chat_renderer import (
    ITALIC,
    TTY,
    B,
    D,
    M,
    ProgressRenderState,
    R,
    X,
    Y,
    finish_stream,
    format_run_summary,
    make_progress,
)
from chat_sessions import Session
from chat_sessions import list_resumable as _list_resumable
from core import config, db

# Аккаунты читаем напрямую из БД, НЕ через handlers.repo: пакет handlers/__init__
# тянет все telegram-хендлеры (aiogram) — лишняя тяжёлая зависимость для CLI, и
# на старом Python она падала на аннотациях, роняя запуск чата из меню.


def _db_accounts():
    with db.conn() as c:
        return list(c.execute("SELECT * FROM accounts WHERE enabled=1 ORDER BY id"))


def _db_account_by_label(label: str):
    with db.conn() as c:
        return c.execute("SELECT * FROM accounts WHERE label=? AND enabled=1", (label,)).fetchone()


def _db_users():
    with db.conn() as c:
        return list(c.execute("SELECT * FROM users ORDER BY created_at"))


_TTY = TTY


# Градиент логотипа: розовый → фиолетовый (256-цветная палитра xterm).
_LOGO_SHADES = [183, 177, 141, 135, 129, 93]


def _logo() -> None:
    art = [
        "██╗  ██╗███████╗██████╗ ███████╗",
        "██║  ██║██╔════╝██╔══██╗██╔════╝",
        "███████║█████╗  ██████╔╝█████╗  ",
        "██╔══██║██╔══╝  ██╔══██╗██╔══╝  ",
        "██║  ██║███████╗██║  ██║███████╗",
        "╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝",
    ]
    print()
    for ln, shade in zip(art, _LOGO_SHADES):
        color = f"\033[38;5;{shade}m" if _TTY else ""
        print(f"  {B}{color}{ln}{X}")
    print(
        f"  {D}·  A S S I S T A N T  ·{X}  {M}v{config.APP_VERSION}{X}"
        f"  {D}·  терминальный чат с CLI-агентом{X}\n"
    )


# ---------- выбор аккаунта ----------
def _pick_account(preselect: str | None):
    accounts = [a for a in _db_accounts() if a["enabled"]]
    if not accounts:
        print(f"{R}Нет подключённых аккаунтов.{X} Добавь через: python manage.py")
        sys.exit(1)
    if preselect:
        for a in accounts:
            if a["label"] == preselect:
                return a
        print(f"{R}Аккаунт '{preselect}' не найден.{X}")
    if len(accounts) == 1:
        return accounts[0]
    print(f"{B}Аккаунты:{X}")
    for i, a in enumerate(accounts, 1):
        owner = "" if a["owner_user_id"] is None else f" {D}(личный){X}"
        print(
            f"  {B}{i}{X}. {a['label']}  {D}{a['provider']} · {a['default_model'] or '—'}{X}{owner}"
        )
    while True:
        try:
            raw = input(f"{D}номер аккаунта › {X}").strip()
        except EOFError:
            print(f"\n{D}ввод закрыт — беру первый аккаунт{X}")
            return accounts[0]
        if raw.isdigit() and 1 <= int(raw) <= len(accounts):
            return accounts[int(raw) - 1]
        print(f"{R}нет такого номера{X}")


# ---------- выбор пользователя ----------
def _pick_user(preselect: str | None):
    """От чьего имени работаем: сессии, workspace и архив припишутся ему.
    Порядок: -u <id|@username> → env HEREASSISTANT_USER → один юзер — сразу он →
    иначе интерактивный выбор. Возвращает (user_id, display)."""
    users = _db_users()
    if not users:
        # БД юзеров пуста (свежая установка) — работаем как главный админ
        return config.ADMIN_ID or 0, "admin"
    for key, src in ((preselect, "-u"), (os.environ.get("HEREASSISTANT_USER", "").strip(), "env")):
        if key:
            u = _find_user(users, key)
            if u:
                return u["telegram_id"], _user_display(u)
            print(f"{R}пользователь '{key}' не найден{X} {D}({src}){X}")
    if len(users) == 1:
        return users[0]["telegram_id"], _user_display(users[0])
    print(f"{B}Кто работает?{X} {D}(сессии и workspace будут его){X}")
    for i, u in enumerate(users, 1):
        role = f" {D}· {u['role']}{X}" if u["role"] != "user" else ""
        print(f"  {B}{i}{X}. {_user_display(u)}{role}")
    while True:
        try:
            raw = input(f"{D}номер › {X}").strip()
        except EOFError:
            print(f"\n{D}ввод закрыт — беру первого{X}")
            return users[0]["telegram_id"], _user_display(users[0])
        if raw.isdigit() and 1 <= int(raw) <= len(users):
            u = users[int(raw) - 1]
            return u["telegram_id"], _user_display(u)
        print(f"{R}нет такого номера{X}")


async def _run_prompt(sess: Session, prompt: str):
    state = ProgressRenderState()
    prov = providers.make(sess.account, user_id=sess.user_id)
    t0 = time.time()
    try:
        text, new_session, meta = await prov.run(
            prompt,
            sess.cwd,
            sess.session_id,
            sess.model,
            progress=make_progress(state),
        )
    except Exception as e:
        print(f"\n{R}✗ Ошибка: {e}{X}")
        return
    finish_stream(state, text)
    sess.session_id = new_session or sess.session_id
    sess.last_meta = meta or {}
    sys.stdout.write(format_run_summary(meta, time.time() - t0))


# ---------- прощание ----------
FAREWELLS = [
    "пока! возвращайся с новой идеей ✨",
    "сессия не потерялась — /resume вернёт всё как было 👋",
    "ушёл в фоновые размышления…",
    "до связи! коммиты не забудь 😉",
    "закрываю терминал, открываю космос 🚀",
    "агент отдыхает. ты тоже отдохни 🌙",
    "конец связи. история — в .jsonl, совесть — чиста 💾",
    "свернулся до следующего промпта 🌀",
    "это была хорошая сессия. увидимся 🤝",
]


def _farewell():
    """Прощальная фраза с эффектом печатной машинки (в TTY — анимированно)."""
    msg = random.choice(FAREWELLS)
    if not _TTY:
        print(msg)
        return
    sys.stdout.write(f"\n{M}▸{X} {ITALIC}")
    for ch in msg:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(0.014)
    print(X)


# ---------- REPL ----------
async def _repl(sess: Session):
    commands = CommandRouter(
        account_by_label=_db_account_by_label,
        users=_db_users,
        default_cwd=config.user_default_cwd,
        resumable=_list_resumable,
    )
    commands.status(sess)
    print(f"\n{D}/help — команды · /exit — выход{X}")
    loop = asyncio.get_event_loop()
    while True:
        try:
            prompt_str = f"\n{B}{M}›{X} "
            line = await loop.run_in_executor(None, lambda: input(prompt_str))
        except (EOFError, KeyboardInterrupt):
            _farewell()
            return
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            if not commands.handle(sess, line):
                _farewell()
                return
            continue
        try:
            await _run_prompt(sess, line)
        except KeyboardInterrupt:
            print(f"\n{Y}⏹ прервано{X}")


def _arg_after(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _run():
    db.init()
    argv = sys.argv[1:]
    _logo()
    user_id, user_name = _pick_user(_arg_after(argv, "-u"))
    account = _pick_account(_arg_after(argv, "-a"))
    sess = Session(account, user_id, user_name)
    asyncio.run(_repl(sess))


def main():
    # Дружелюбно к ошибкам: не роняем сырой traceback (при запуске из меню
    # manage.py он мелькал и стирался перерисовкой). Показываем причину и,
    # если это терминал, ждём Enter — чтобы её можно было прочитать.
    try:
        _run()
    except KeyboardInterrupt:
        pass
    except EOFError:
        pass  # ввод закрыт (не-интерактивный запуск) — тихо выходим
    except Exception as e:
        import traceback

        print(f"\n{R}✗ Не удалось запустить чат: {e}{X}")
        print(f"{D}{traceback.format_exc().strip()}{X}")
        print(
            f"\n{Y}Подсказка:{X} запускай тем же Python, что и бот "
            f"(в проекте: {D}.venv/bin/python chat.py{X}), из папки бота "
            f"(где лежит bridge.sqlite3). Аккаунты добавляются в {D}manage.py{X}."
        )
        if sys.stdin.isatty():
            try:
                input(f"\n{D}Enter — закрыть…{X}")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    main()
