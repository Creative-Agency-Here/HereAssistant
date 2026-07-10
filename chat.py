#!/usr/bin/env python3
"""HereAssistant CLI — интерактивный терминальный чат с CLI-агентом.

То же, что бот в Telegram, но прямо в консоли: окно ввода, полный живой вывод
(рассуждения модели, вызовы инструментов и их результаты, диффы правок — без
лимита Telegram), продолжение прошлых сессий (/resume), настройки слэш-командами.
Работает поверх тех же подписок-аккаунтов HereAssistant (Claude / Codex / Gemini),
так что квота и вход — общие с ботом.

Запуск:  python chat.py            — выбрать аккаунт интерактивно
         python chat.py -a <label> — сразу на аккаунте
Внутри:  /help — список команд.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

from core import config, db
from handlers import repo
import providers

# --- ANSI (как в manage.py; при отсутствии TTY гасим) ---
_TTY = sys.stdout.isatty()
def _c(code: str) -> str:
    return code if _TTY else ""
G = _c("\033[92m"); R = _c("\033[91m"); Y = _c("\033[93m"); C = _c("\033[96m")
M = _c("\033[95m"); W = _c("\033[97m"); B = _c("\033[1m"); D = _c("\033[2m")
I = _c("\033[3m"); X = _c("\033[0m")

STEP_ICON = {"run": f"{Y}⏺{X}", "ok": f"{G}✓{X}", "err": f"{R}✗{X}"}


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
    for ln in art:
        print(f"  {B}{M}{ln}{X}")
    print(f"  {D}·  A S S I S T A N T  ·  терминальный чат с CLI-агентом{X}\n")


class Session:
    """Состояние одной терминальной сессии: аккаунт, модель, папка, id сессии."""

    def __init__(self, account, user_id: int):
        self.account = account            # sqlite3.Row аккаунта
        self.user_id = user_id
        self.model = account["default_model"]
        self.cwd = config.user_default_cwd(user_id)
        self.session_id = None            # provider_session_id для --resume
        self.last_meta = {}               # meta последнего ответа (для /diff)

    @property
    def label(self) -> str:
        return self.account["label"]

    @property
    def provider(self) -> str:
        return self.account["provider"]


# ---------- выбор аккаунта ----------
def _pick_account(preselect: str | None):
    accounts = [a for a in repo.list_accounts() if a["enabled"]]
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
        print(f"  {B}{i}{X}. {a['label']}  {D}{a['provider']} · {a['default_model'] or '—'}{X}{owner}")
    while True:
        raw = input(f"{D}номер аккаунта › {X}").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(accounts):
            return accounts[int(raw) - 1]
        print(f"{R}нет такого номера{X}")


# ---------- нативный стор сессий Claude (для /resume) ----------
def _claude_sessions_dir(sess: Session) -> Path | None:
    """Каталог нативных .jsonl-сессий Claude Code для текущего аккаунта+cwd."""
    if sess.provider != "claude_code":
        return None
    slug = str(sess.cwd).replace("/", "-").replace("\\", "-")
    d = Path(sess.account["cli_home_path"]) / "projects" / slug
    return d if d.exists() else None


def _list_resumable(sess: Session):
    """Список прошлых сессий текущего проекта: (session_id, заголовок, mtime)."""
    d = _claude_sessions_dir(sess)
    if not d:
        return []
    import json
    out = []
    for f in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        title = ""
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("type") == "user" and not o.get("isMeta"):
                        msg = o.get("message", {})
                        content = msg.get("content")
                        if isinstance(content, str):
                            title = content
                        elif isinstance(content, list):
                            for b in content:
                                if isinstance(b, dict) and b.get("type") == "text":
                                    title = b.get("text", "")
                                    break
                        if title.strip():
                            break
        except Exception:
            pass
        out.append((f.stem, title.strip()[:70] or "(без текста)", f.stat().st_mtime))
    return out[:20]


# ---------- рендер стрима в терминал ----------
def _make_progress(state: dict):
    """Async-callback для provider.run: печатает события по мере прихода —
    рассуждения (серым), вызовы инструментов (⏺), их результаты (⎿), текст ответа."""
    async def progress(text: str, event_type: str, meta: dict):
        # 1) рассуждения модели — серым хвостом
        th = meta.get("thinking") or ""
        if len(th) > state["thinking_len"]:
            chunk = th[state["thinking_len"]:]
            state["thinking_len"] = len(th)
            if not state["thinking_shown"]:
                sys.stdout.write(f"\n{D}{I}💭 ")
                state["thinking_shown"] = True
            sys.stdout.write(f"{D}{I}{chunk}{X}")
        # 2) шаги: печатаем вызов инструмента, когда шаг завершён (статус ⏺→✓/✗
        # или пришёл результат) — к этому моменту описание уже с полными
        # аргументами (на старте content_block_start input часто пуст).
        steps = meta.get("steps") or []
        for idx, st in enumerate(steps):
            key = st.get("id") or f"i{idx}"
            done = st.get("status") != "run" or st.get("result")
            if done and key not in state["printed_tools"]:
                state["printed_tools"].add(key)
                _flush_text(state)
                icon = STEP_ICON.get(st.get("status"), f"{Y}⏺{X}")
                sys.stdout.write(f"\n{icon} {W}{st.get('desc')}{X}")
                res = st.get("result")
                if res:
                    r = str(res)
                    if len(r) > 400:
                        r = r[:400] + "…"
                    sys.stdout.write(f"\n   {D}⎿ {r}{X}")
        # 3) текст ответа — печатаем дельту
        if event_type in ("assistant_delta", "partial_delta") and text:
            state["pending_text"] = text
            _flush_text(state)
        sys.stdout.flush()
    return progress


def _flush_text(state: dict):
    """Допечатать хвост накопленного текста ответа (дельту к уже показанному)."""
    text = state.get("pending_text") or ""
    shown = state["text_len"]
    if len(text) <= shown:
        return
    if not text.startswith(state["text_prefix"]):
        # редкий случай: провайдер переустановил текст — начинаем абзац заново
        sys.stdout.write("\n")
        shown = 0
    if not state["answer_started"]:
        sys.stdout.write(f"\n{C}▌{X} ")
        state["answer_started"] = True
    sys.stdout.write(text[shown:])
    state["text_len"] = len(text)
    state["text_prefix"] = text[:200]


async def _run_prompt(sess: Session, prompt: str):
    state = {
        "thinking_len": 0, "thinking_shown": False,
        "printed_tools": set(), "printed_res": set(),
        "pending_text": "", "text_len": 0, "text_prefix": "", "answer_started": False,
    }
    prov = providers.make(sess.account)
    t0 = time.time()
    try:
        text, new_session, meta = await prov.run(
            prompt, sess.cwd, sess.session_id, sess.model, progress=_make_progress(state),
        )
    except Exception as e:
        print(f"\n{R}✗ Ошибка: {e}{X}")
        return
    # Финальный текст (если стрима не было / rich-путь его не печатал)
    _flush_text(dict(state, pending_text=text))
    if not state["answer_started"] and text:
        sys.stdout.write(f"\n{C}▌{X} {text}")
    sess.session_id = new_session or sess.session_id
    sess.last_meta = meta or {}
    # Подпись: правки + токены + время
    edits = meta.get("edits") or []
    dur = time.time() - t0
    bits = [f"{dur:.0f}с"]
    if edits:
        add = sum(e.get("added", 0) for e in edits)
        rem = sum(e.get("removed", 0) for e in edits)
        bits.append(f"{G}+{add}{X} {R}−{rem}{X} в {len(edits)} файл.")
    tin, tout = meta.get("tokens_in"), meta.get("tokens_out")
    if tin or tout:
        bits.append(f"токены {tin or 0}/{tout or 0}")
    print(f"\n{D}— {' · '.join(bits)}{X}\n")


# ---------- слэш-команды ----------
def _print_help():
    print(f"""{B}Команды:{X}
  {C}/help{X}              эта справка
  {C}/model{X} [имя]       показать/сменить модель
  {C}/account{X} [label]   показать/сменить аккаунт (сбрасывает сессию)
  {C}/cwd{X} [путь]        показать/сменить рабочую папку
  {C}/new{X}               начать новую сессию (забыть контекст)
  {C}/resume{X}            выбрать и продолжить прошлую сессию проекта
  {C}/status{X}            аккаунт, модель, папка, id сессии
  {C}/diff{X}              диффы правок последнего ответа
  {C}/clear{X}             очистить экран
  {C}/exit{X} (или Ctrl+D) выход
{D}Просто пиши текст — это уйдёт агенту. Многострочность: заканчивай пустой строкой не нужно, отправляется по Enter.{X}""")


def _cmd_status(sess: Session):
    print(f"  {D}аккаунт{X}  {sess.label}  {D}({sess.provider}){X}")
    print(f"  {D}модель {X}  {sess.model or '—'}")
    print(f"  {D}папка  {X}  {sess.cwd}")
    print(f"  {D}сессия {X}  {sess.session_id or '(новая)'}")


def _cmd_resume(sess: Session):
    items = _list_resumable(sess)
    if not items:
        print(f"{D}Прошлых сессий для этого проекта не найдено "
              f"(или провайдер не поддерживает).{X}")
        return
    print(f"{B}Прошлые сессии этого проекта:{X}")
    for i, (sid, title, mtime) in enumerate(items, 1):
        ago = time.strftime("%d.%m %H:%M", time.localtime(mtime))
        cur = f" {G}← текущая{X}" if sid == sess.session_id else ""
        print(f"  {B}{i}{X}. {title}  {D}{ago} · {sid[:8]}{X}{cur}")
    raw = input(f"{D}номер (Enter — отмена) › {X}").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        sess.session_id = items[int(raw) - 1][0]
        print(f"{G}▸ продолжаю сессию {sess.session_id[:8]}{X}")


def _handle_command(sess: Session, line: str) -> bool:
    """Обработать слэш-команду. Возвращает False, если пора выходить."""
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/exit", "/quit", "/q"):
        return False
    if cmd == "/help":
        _print_help()
    elif cmd == "/status":
        _cmd_status(sess)
    elif cmd == "/model":
        if arg:
            sess.model = arg
            print(f"{G}▸ модель: {arg}{X}")
        else:
            print(f"  модель: {sess.model or '—'}  {D}(/model <имя> — сменить){X}")
    elif cmd == "/account":
        if arg:
            a = repo.get_account_by_label(arg)
            if a and a["enabled"]:
                sess.account = a
                sess.model = a["default_model"]
                sess.session_id = None
                print(f"{G}▸ аккаунт: {arg} · {a['provider']} (сессия сброшена){X}")
            else:
                print(f"{R}аккаунт '{arg}' не найден/выключен{X}")
        else:
            print(f"  аккаунт: {sess.label} {D}({sess.provider}){X}  {D}(/account <label>){X}")
    elif cmd == "/cwd":
        if arg:
            p = Path(os.path.expanduser(arg))
            if p.is_dir():
                sess.cwd = str(p.resolve())
                sess.session_id = None  # смена папки → другой проект → новая сессия
                print(f"{G}▸ папка: {sess.cwd} (сессия сброшена){X}")
            else:
                print(f"{R}нет такой папки: {arg}{X}")
        else:
            print(f"  папка: {sess.cwd}  {D}(/cwd <путь>){X}")
    elif cmd == "/new":
        sess.session_id = None
        print(f"{G}▸ новая сессия — контекст забыт{X}")
    elif cmd == "/resume":
        _cmd_resume(sess)
    elif cmd == "/diff":
        _cmd_diff(sess)
    elif cmd == "/clear":
        os.system("cls" if os.name == "nt" else "clear")
    else:
        print(f"{R}неизвестная команда {cmd}{X} — /help")
    return True


def _cmd_diff(sess: Session):
    edits = (sess.last_meta or {}).get("edits") or []
    if not edits:
        print(f"{D}В последнем ответе правок файлов не было.{X}")
        return
    for e in edits:
        f = e.get("file", "?")
        print(f"\n{B}{f}{X}  {G}+{e.get('added', 0)}{X} {R}−{e.get('removed', 0)}{X}")
        old = (e.get("old") or "").splitlines()
        new = (e.get("new") or "").splitlines()
        for ln in old[:40]:
            print(f"{R}- {ln}{X}")
        for ln in new[:40]:
            print(f"{G}+ {ln}{X}")
        if len(old) > 40 or len(new) > 40:
            print(f"{D}  …(обрезано, полностью — в вебапе правок){X}")


# ---------- REPL ----------
async def _repl(sess: Session):
    _cmd_status(sess)
    print(f"\n{D}/help — команды · /exit — выход{X}")
    loop = asyncio.get_event_loop()
    while True:
        try:
            prompt_str = f"\n{B}{M}›{X} "
            line = await loop.run_in_executor(None, lambda: input(prompt_str))
        except (EOFError, KeyboardInterrupt):
            print(f"\n{D}пока{X}")
            return
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            if not _handle_command(sess, line):
                print(f"{D}пока{X}")
                return
            continue
        try:
            await _run_prompt(sess, line)
        except KeyboardInterrupt:
            print(f"\n{Y}⏹ прервано{X}")


def main():
    db.init()
    preselect = None
    argv = sys.argv[1:]
    if "-a" in argv:
        i = argv.index("-a")
        if i + 1 < len(argv):
            preselect = argv[i + 1]
    _logo()
    account = _pick_account(preselect)
    user_id = config.ADMIN_ID or 0
    sess = Session(account, user_id)
    try:
        asyncio.run(_repl(sess))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
