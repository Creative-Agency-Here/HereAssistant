"""
HereAssistant — единый менеджер.

Запуск:
    python manage.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bridge.sqlite3"
RUNTIME_DIR = BASE_DIR / ".runtime"
CLI_HOMES_DIR = RUNTIME_DIR / "cli_homes"
ENV_PATH = BASE_DIR / ".env"

PROVIDERS = {
    "1": {
        "key": "claude_code",
        "title": "Claude Code",
        "subtitle": "Anthropic",
        "bin": "claude",
        "npm_pkg": "@anthropic-ai/claude-code",
        "env_var": "CLAUDE_CONFIG_DIR",
        "default_model": "claude-opus-4-7",
        "login_hint": "В TUI пройди /login и выбери свой Max/Pro аккаунт. Затем /exit.",
    },
    "2": {
        "key": "codex",
        "title": "Codex CLI",
        "subtitle": "OpenAI",
        "bin": "codex",
        "npm_pkg": "@openai/codex",
        "env_var": "CODEX_HOME",
        "default_model": "gpt-5",
        "login_hint": "Откроется браузер для OAuth — авторизуйся в нужном OpenAI-аккаунте.",
    },
    "3": {
        "key": "gemini",
        "title": "Gemini CLI",
        "subtitle": "Google",
        "bin": "gemini",
        "npm_pkg": "@google/gemini-cli",
        "env_var": "USERPROFILE" if os.name == "nt" else "HOME",
        "default_model": "gemini-2.5-pro",
        "login_hint": "В TUI пройди /auth и авторизуйся в Google-аккаунте. Затем /exit.",
    },
}

# ANSI
if os.name == "nt":
    os.system("")  # включить ANSI на Windows
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
M = "\033[95m"; W = "\033[97m"; B = "\033[1m"; D = "\033[2m"; X = "\033[0m"
BG_G = "\033[42m"; BG_R = "\033[41m"; BG_Y = "\033[43m"; BG_B = "\033[44m"
BLACK = "\033[30m"


# ---------- однокнопочный ввод ----------
def getch() -> str:
    """Один символ без Enter. Возвращает строку (для совместимости с input())."""
    try:
        if os.name == "nt":
            import msvcrt
            ch = msvcrt.getwch()
            return ch
        else:
            import termios, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            return ch
    except Exception:
        # fallback: обычный input
        return input().strip()[:1]


def press_any_key(prompt: str = "Нажми любую клавишу для меню..."):
    print(f"\n{D}{prompt}{X}", end="", flush=True)
    getch()
    print()


# ---------- БД ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id  INTEGER PRIMARY KEY, username TEXT,
    role TEXT NOT NULL DEFAULT 'user', created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,
    label TEXT NOT NULL UNIQUE, cli_home_path TEXT NOT NULL,
    default_model TEXT, enabled INTEGER NOT NULL DEFAULT 1, notes TEXT,
    owner_user_id INTEGER
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL DEFAULT 0,
    account_id INTEGER, model TEXT, provider_session_id TEXT, cwd TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    UNIQUE (chat_id, thread_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL,
    provider TEXT, model TEXT, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


def db_init():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    CLI_HOMES_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        # Миграция: колонка владельца аккаунта (для старых БД).
        cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
        if "owner_user_id" not in cols:
            try:
                conn.execute("ALTER TABLE accounts ADD COLUMN owner_user_id INTEGER")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def run_visible(argv: list[str], env_extra: dict = None) -> int:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.call(argv, env=env)


def npm_install(pkg: str) -> bool:
    npm = shutil.which("npm")
    if not npm:
        print(f"{R}✗ npm не найден. Поставь Node.js с https://nodejs.org{X}")
        return False
    if os.name == "nt" and npm.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c", "npm", "install", "-g", pkg]
    else:
        argv = ["npm", "install", "-g", pkg]
    return run_visible(argv) == 0


# ---------- .env ----------
def env_template() -> str:
    return (
        "# Telegram-токен (новый, после revoke у @BotFather)\n"
        "TELEGRAM_BOT_TOKEN=PASTE_HERE\n\n"
        "# Telegram user_id админа. Можно оставить пустым —\n"
        "# тогда при первом запуске бота в консоли появится\n"
        "# claim-ссылка. Откроешь её — бот сам впишет твой id сюда.\n"
        "ADMIN_TELEGRAM_ID=\n\n"
        f"DEFAULT_CWD={Path.home()}\n"
        "CLI_TIMEOUT_SEC=1800\n"
        "MAX_HISTORY=20\n\n"
        "# --- Claude Code ---\n"
        "# Что разрешено CLI без подтверждения: acceptEdits | bypassPermissions | default\n"
        "CLAUDE_PERMISSION_MODE=acceptEdits\n"
        "# Если =1 — будет писать сырые stream-json события в .runtime/logs/claude-stream-*.jsonl\n"
        "# для отладки парсера. Включи если стрим не работает.\n"
        "CLAUDE_DEBUG_STREAM=0\n\n"
        "# --- Прогресс-стриминг ---\n"
        "PROGRESS_ENABLED=1\n"
        "PROGRESS_MIN_INTERVAL_SEC=1.5\n"
        "TYPING_INTERVAL_SEC=4\n\n"
        "# --- Прерывание ---\n"
        "# Если =1 — новое сообщение от админа отменяет текущую задачу\n"
        "# Если =0 — ставится в очередь (старое поведение)\n"
        "INTERRUPT_ON_NEW_MESSAGE=1\n"
    )


def ensure_env():
    if not ENV_PATH.exists():
        ENV_PATH.write_text(env_template(), encoding="utf-8")


def env_state() -> dict:
    """Возвращает раздельный статус по полям."""
    out = {
        "exists": ENV_PATH.exists(),
        "token_set": False,
        "admin_set": False,
        "claim_pending": False,
    }
    if not out["exists"]:
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip(); v = v.strip()
        if k == "TELEGRAM_BOT_TOKEN":
            out["token_set"] = bool(v) and v != "PASTE_HERE"
        elif k in ("ADMIN_IDS", "ADMIN_TELEGRAM_ID"):
            # админ задан, если есть хоть один валидный числовой id
            ids = [p.strip() for p in v.replace(";", ",").split(",")]
            if any(p and p != "PASTE_HERE" and p.lstrip("-").isdigit() for p in ids):
                out["admin_set"] = True
        elif k == "CLAIM_CODE":
            out["claim_pending"] = bool(v)
    return out


# ---------- аккаунты ----------
def list_accounts() -> list[sqlite3.Row]:
    with db() as conn:
        return list(conn.execute("SELECT * FROM accounts ORDER BY id"))


def is_logged_in(provider_key: str, cli_home: Path) -> tuple[bool, str]:
    if not cli_home.exists():
        return False, ""
    if provider_key == "claude_code":
        for p in [cli_home / ".credentials.json",
                  cli_home / "credentials.json",
                  cli_home / ".claude" / ".credentials.json"]:
            if p.exists():
                return True, p.name
        return False, ""
    if provider_key == "codex":
        for p in [cli_home / "auth.json", cli_home / ".codex" / "auth.json"]:
            if p.exists():
                return True, p.name
        return False, ""
    if provider_key == "gemini":
        for p in [cli_home / ".gemini" / "oauth_creds.json",
                  cli_home / ".gemini" / "credentials.json"]:
            if p.exists():
                return True, p.parent.name + "/" + p.name
        return False, ""
    return False, ""


# ---------- UI helpers ----------
def line(char: str = "─", width: int = 64, color: str = D) -> str:
    return f"{color}{char * width}{X}"


def box_top(width: int = 64) -> str:
    return f"{C}╭{'─' * (width - 2)}╮{X}"


def box_bot(width: int = 64) -> str:
    return f"{C}╰{'─' * (width - 2)}╯{X}"


def box_mid(text: str, width: int = 64, color: str = "") -> str:
    visible = strip_ansi(text)
    pad = max(0, width - 4 - len(visible))
    return f"{C}│{X} {color}{text}{X}{' ' * pad} {C}│{X}"


def strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


def badge(text: str, fg: str = BLACK, bg: str = BG_G) -> str:
    return f"{bg}{fg} {text} {X}"


# ---------- инфо для шапки ----------

_BOT_CACHE = {"done": False, "username": None}


def _env_val(key: str) -> str:
    """Прочитать значение ключа из .env (без внешних зависимостей)."""
    if not ENV_PATH.exists():
        return ""
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def _bot_username():
    """@username бота через getMe (один сетевой запрос за процесс, кэшируется)."""
    if _BOT_CACHE["done"]:
        return _BOT_CACHE["username"]
    _BOT_CACHE["done"] = True
    token = _env_val("TELEGRAM_BOT_TOKEN")
    if not token or token == "PASTE_HERE":
        return None
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getMe", timeout=4
        ) as r:
            d = json.load(r)
        if d.get("ok"):
            _BOT_CACHE["username"] = "@" + d["result"]["username"]
    except Exception:
        pass
    return _BOT_CACHE["username"]


def _admin_ids() -> list[str]:
    """Список Telegram-ID админов из .env (ADMIN_IDS или легаси ADMIN_TELEGRAM_ID)."""
    raw = _env_val("ADMIN_IDS") or _env_val("ADMIN_TELEGRAM_ID")
    return [
        p.strip()
        for p in raw.replace(";", ",").split(",")
        if p.strip() and p.strip() != "PASTE_HERE" and p.strip().lstrip("-").isdigit()
    ]


def _account_usage(label: str) -> dict:
    """Потребление аккаунта за окно лимита подписки (5ч — как у Claude Max):
    сколько запросов и токенов, и не прилетал ли недавно rate-limit."""
    out = {"msgs": 0, "tokens": 0, "limited": False, "reset": None}
    try:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        cutoff = int(time.time()) - 5 * 3600
        row = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0) tok "
            "FROM events WHERE account_label=? AND event_type='message_out' AND timestamp>=?",
            (label, cutoff),
        ).fetchone()
        out["msgs"] = row["n"] or 0
        out["tokens"] = row["tok"] or 0
        # Недавний rate-limit (последний час) — признак упёртого лимита.
        rl = c.execute(
            "SELECT payload,timestamp FROM events WHERE account_label=? "
            "AND (event_type='rate_limit' OR payload LIKE '%rate_limit%') "
            "ORDER BY id DESC LIMIT 1",
            (label,),
        ).fetchone()
        c.close()
        if rl and rl["timestamp"] >= int(time.time()) - 3600:
            out["limited"] = True
            try:
                pl = json.loads(rl["payload"] or "{}")
                out["reset"] = pl.get("rate_limit_reset") or pl.get("reset")
            except Exception:
                pass
    except Exception:
        pass
    return out


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.0f}k"
    return str(n)


def logo():
    """Логотип в шапке: вордмарк HERE (ANSI-Shadow) в фиолетовом #AB60F6 + тэглайн."""
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
    print(f"  {D}·  A S S I S T A N T  ·  мульти-CLI Telegram-мост{X}")


def header():
    # ANSI-очистка вместо `clear`/`cls` — не читает terminfo, поэтому нет
    # предупреждения «xterm-ghostty: unknown terminal type».
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()
    logo()
    print(box_top())
    title = f"{B}{M}HereAssistant{X}{D} — мульти-CLI Telegram-мост{X}"
    print(box_mid(title))
    print(box_mid(f"{D}проект: {BASE_DIR}{X}"))
    print(f"{C}├{'─' * 62}┤{X}")

    es = env_state()

    # Бот — конкретное @имя (не абстрактный «OK»)
    bot = _bot_username()
    if bot:
        print(box_mid(f"Бот        {G}{bot}{X}"))
    elif es.get("token_set"):
        print(box_mid(f"Бот        {G}токен есть{X} {D}(имя не получено — нет сети?){X}"))
    else:
        print(box_mid(f"Бот        {R}токен не задан{X} {D}— пункт «Настройки → .env»{X}"))

    # Админ — конкретные ID
    ids = _admin_ids()
    if ids:
        print(box_mid(f"Админ      {G}{', '.join(ids)}{X}"))
    else:
        print(box_mid(f"Админ      {Y}не задан{X} {D}(станет админом первый /start){X}"))

    # Аккаунты — по-простому: «claude · залогинен · юзаж за 5ч»
    accs = list_accounts()
    if not accs:
        print(box_mid(f"Аккаунт    {Y}нет{X} {D}— добавь пункт [2]{X}"))
    else:
        for r in accs:
            p = next((v for v in PROVIDERS.values() if v["key"] == r["provider"]), None)
            name = p["bin"] if p else r["provider"]
            owner = r["owner_user_id"] if "owner_user_id" in r.keys() else None
            who = f"{D}владелец {owner}{X}" if owner else f"{D}общий{X}"
            logged = p and is_logged_in(p["key"], Path(r["cli_home_path"]))[0]
            if not logged:
                print(box_mid(f"Аккаунт    {B}{name}{X} · {who} · {R}нет входа{X}"))
                continue
            u = _account_usage(r["label"])
            usage = f"{D}за 5ч: {u['msgs']} запр · {_fmt_tokens(u['tokens'])} ток{X}"
            if u["limited"]:
                reset = f" до {u['reset']}" if u["reset"] else ""
                usage = f"{R}лимит подписки{reset}{X}"
            print(box_mid(f"Аккаунт    {B}{name}{X} · {who} · {G}залогинен{X} · {usage}"))

    print(box_bot())


# Главное меню — только частое. Редкое спрятано в «Настройки».
# (клавиша, иконка+цвет, название, подсказка-тултип)
MENU_MAIN = [
    ("1", f"{C}▸{X}", "Аккаунты", "список подключённых CLI-аккаунтов и вход"),
    ("2", f"{G}+{X}", "Добавить аккаунт", "подключить и залогинить Claude / Codex / Gemini"),
    ("3", f"{C}≡{X}", "История · Аудит", "все обращения в Telegram и SSH-заходы по IP"),
    ("8", f"{G}▶{X}", "Запустить бота", "поднять бота (Ctrl+C — остановить)"),
    ("9", f"{M}⚙{X}", "Настройки", "перелогин, удаление, зависимости, .env"),
    ("0", f"{D}⏻{X}", "Выход", ""),
]
MENU_SETTINGS = [
    ("1", f"{Y}↻{X}", "Перелогиниться", "обновить вход существующего аккаунта"),
    ("2", f"{R}×{X}", "Удалить аккаунт", "отключить аккаунт и стереть его вход"),
    ("3", f"{C}▦{X}", "Файлы аккаунтов", "что лежит в .runtime/cli_homes"),
    ("4", f"{M}⚙{X}", "Зависимости", "поставить/обновить pip + npm"),
    ("5", f"{C}✎{X}", "Открыть .env", "токен бота, админы, настройки"),
    ("0", f"{D}←{X}", "Назад", ""),
]


def render_menu(items):
    print()
    for k, icon, name, desc in items:
        tip = f"   {D}— {desc}{X}" if desc else ""
        print(f"  {B}[{k}]{X}  {icon}  {name}{tip}")
    print(f"\n{D}нажми клавишу (без Enter){X} › ", end="", flush=True)
    return getch()


# ---------- действия ----------
def _fmt_ts(ts: int) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def show_history():
    """История/аудит: все обращения в Telegram (кто/когда/аккаунт) + SSH-заходы по IP."""
    print(f"\n{B}{M}История · Аудит{X}")

    # 1) Обращения в Telegram — из events (метаданные пишутся ВСЕГДА, даже для
    #    private-проектов; содержимого может не быть, но факт/кто/когда/аккаунт — есть).
    print(f"\n{B}Обращения в Telegram{X} {D}(кто · когда · какой аккаунт){X}")
    print(line(width=64))
    try:
        c = db()
        rows = list(c.execute(
            "SELECT timestamp, event_type, user_id, account_label, "
            "COALESCE(tokens_in,0)+COALESCE(tokens_out,0) tok "
            "FROM events WHERE event_type IN ('message_in','message_out','error') "
            "ORDER BY id DESC LIMIT 30"
        ))
        c.close()
    except Exception:
        rows = []
    if not rows:
        print(f"  {D}пока пусто — боту ещё не писали{X}")
    else:
        labels = {"message_in": f"{C}→ запрос{X}", "message_out": f"{G}✓ ответ{X}",
                  "error": f"{R}✗ ошибка{X}"}
        for r in rows:
            et = labels.get(r["event_type"], r["event_type"])
            acc = r["account_label"] or "-"
            toks = f" · {_fmt_tokens(r['tok'])} ток" if r["tok"] else ""
            print(f"  {D}{_fmt_ts(r['timestamp'])}{X}  {et:<18} "
                  f"user {B}{r['user_id']}{X}  {M}{acc}{X}{D}{toks}{X}")

    # 2) SSH-заходы на сервер — из системного журнала логинов (last). Кто (Unix-
    #    юзер), с какого IP, когда. Разные люди = разные Unix-юзеры → видно кто.
    print(f"\n{B}SSH-заходы на сервер{X} {D}(юзер · IP · когда){X}")
    print(line(width=64))
    try:
        out = subprocess.run(["last", "-a", "-n", "15"],
                             capture_output=True, text=True, timeout=5).stdout
        shown = [l for l in out.splitlines() if l.strip() and not l.lower().startswith("wtmp")]
        if shown:
            for l in shown[:15]:
                print(f"  {D}{l}{X}")
        else:
            print(f"  {D}нет данных{X}")
    except Exception:
        print(f"  {D}команда last недоступна на этой системе{X}")


def show_accounts():
    rows = list_accounts()
    print(f"\n{B}Зарегистрированные аккаунты{X}")
    print(line())
    if not rows:
        print(f"  {Y}пока нет — добавь через пункт меню 2{X}")
        return rows
    print(f"  {B}{'#':<3} {'LABEL':<20} {'PROVIDER':<14} {'MODEL':<22} {'LOGIN':<10} {'NOTE':<20}{X}")
    print(line(width=64))
    for r in rows:
        p = next((v for v in PROVIDERS.values() if v["key"] == r["provider"]), None)
        if p:
            logged, _ = is_logged_in(p["key"], Path(r["cli_home_path"]))
            login_str = badge("есть", BLACK, BG_G) if logged else badge("нет ", BLACK, BG_R)
        else:
            login_str = "?"
        model = (r["default_model"] or "-")[:22]
        note = (r["notes"] or "")[:20]
        print(f"  {r['id']:<3} {r['label']:<20} {r['provider']:<14} {model:<22} "
              f"{login_str:<18} {D}{note}{X}")
    return rows


def show_disk_state():
    print(f"\n{B}Папки в .runtime/cli_homes/{X}")
    print(line())
    if not CLI_HOMES_DIR.exists() or not any(CLI_HOMES_DIR.iterdir()):
        print(f"  {Y}пусто{X}")
        return
    registered_paths = {Path(r["cli_home_path"]).resolve() for r in list_accounts()}
    for item in sorted(CLI_HOMES_DIR.iterdir()):
        if not item.is_dir():
            continue
        is_reg = item.resolve() in registered_paths
        mark = badge("зареган", BLACK, BG_G) if is_reg else badge("сирота ", BLACK, BG_Y)
        prov = item.name.split("__", 1)[0] if "__" in item.name else "?"
        try:
            n_files = sum(1 for f in item.rglob("*") if f.is_file())
        except Exception:
            n_files = "?"
        print(f"  {mark}  {B}{item.name}{X}  {D}({n_files} файлов, {prov}){X}")


def pick_provider() -> dict | None:
    print(f"\n{B}Выбери провайдера{X}  {D}(нажми 1/2/3, или Esc — отмена){X}")
    print(line())
    for k, v in PROVIDERS.items():
        ready = badge("OK ", BLACK, BG_G) if has_cmd(v["bin"]) else badge("нет CLI", W, BG_R)
        print(f"  {B}[{k}]{X}  {ready}  {B}{v['title']}{X} {D}{v['subtitle']}{X}")
    print(f"\n› ", end="", flush=True)
    choice = getch()
    print(choice)
    if choice not in PROVIDERS:
        return None
    return PROVIDERS[choice]


def add_account_interactive():
    print(f"\n{B}{M}Добавить аккаунт{X}\n")
    prov = pick_provider()
    if not prov:
        print("Отмена.")
        return

    if not has_cmd(prov["bin"]):
        print(f"\n{Y}CLI '{prov['bin']}' не установлен.{X}")
        print(f"Поставить через npm? [Y/n] › ", end="", flush=True)
        ans = getch().lower()
        print(ans)
        if ans not in ("n", "т"):
            if not npm_install(prov["npm_pkg"]):
                print(f"{R}✗ установка не удалась{X}")
                return
        else:
            return

    print(f"\n{D}Label — короткое имя (буквы/цифры/-/_). Примеры: main, work, gemini-free{X}")
    label = input(f"{B}Label{X}: ").strip()
    if not label:
        print("Пусто. Отмена.")
        return
    safe = "".join(c for c in label if c.isalnum() or c in "-_")
    if safe != label:
        print(f"{Y}В label оставлены только буквы/цифры/-_: '{safe}'{X}")
        label = safe

    note = input(f"{B}Заметка{X} {D}(email или описание, можно пусто){X}: ").strip()
    model = input(f"{B}Модель{X} {D}(Enter — '{prov['default_model']}'){X}: ").strip() or prov["default_model"]

    # Владелец — Telegram user_id (чей это аккаунт/подписка). Пусто = общий.
    ids = _admin_ids()
    hint = f" {D}(админы: {', '.join(ids)}){X}" if ids else ""
    owner_raw = input(f"{B}Владелец{X} {D}(Telegram user_id; Enter — общий){X}{hint}: ").strip()
    owner = int(owner_raw) if owner_raw.lstrip("-").isdigit() else None

    cli_home = CLI_HOMES_DIR / f"{prov['key']}__{label}"
    cli_home.mkdir(parents=True, exist_ok=True)

    with db() as conn:
        try:
            conn.execute(
                """INSERT INTO accounts(provider, label, cli_home_path, default_model, enabled, notes, owner_user_id)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (prov["key"], label, str(cli_home), model, note, owner),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            print(f"{R}Аккаунт с label='{label}' уже есть.{X}")
            return

    print(f"\n{G}✓ Аккаунт '{label}' зарегистрирован{X}")
    print(f"{D}папка: {cli_home}{X}")

    print(f"\nЗалогиниться сейчас? [Y/n] › ", end="", flush=True)
    ans = getch().lower()
    print(ans)
    if ans not in ("n", "т"):
        do_login(prov, cli_home)


def do_login(prov: dict, cli_home: Path):
    print(f"\n{C}▶ Запускаю {prov['bin']} с изолированным окружением{X}")
    print(f"{D}{prov['env_var']}={cli_home}{X}")
    print(f"{Y}{prov['login_hint']}{X}\n")

    env_extra = {prov["env_var"]: str(cli_home)}
    if prov["key"] == "gemini":
        env_extra["HOME"] = str(cli_home)
        env_extra["USERPROFILE"] = str(cli_home)

    if prov["key"] == "codex":
        argv = [prov["bin"], "login"]
    else:
        argv = [prov["bin"]]

    if os.name == "nt":
        bin_path = shutil.which(prov["bin"])
        if bin_path and bin_path.lower().endswith((".cmd", ".bat")):
            argv = ["cmd", "/c", prov["bin"]] + argv[1:]

    run_visible(argv, env_extra)
    logged, hint = is_logged_in(prov["key"], cli_home)
    if logged:
        print(f"\n{G}✓ Логин зафиксирован ({hint}){X}")
    else:
        print(f"\n{Y}⚠ Не вижу auth-файлов в {cli_home}{X}")
        print(f"{D}Возможно ты не завершил логин в TUI/браузере.{X}")


def login_existing():
    rows = show_accounts()
    if not rows:
        return
    label = input(f"\n{B}Label для перелогина{X} {D}(Enter — отмена){X}: ").strip()
    if not label:
        return
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE label=?", (label,)).fetchone()
    if not row:
        print(f"{R}Не нашёл '{label}'.{X}")
        return
    prov = next((v for v in PROVIDERS.values() if v["key"] == row["provider"]), None)
    if not prov:
        print(f"{R}Неизвестный провайдер.{X}")
        return
    do_login(prov, Path(row["cli_home_path"]))


def remove_account():
    rows = show_accounts()
    if not rows:
        return
    label = input(f"\n{B}Label для удаления{X} {D}(Enter — отмена){X}: ").strip()
    if not label:
        return
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE label=?", (label,)).fetchone()
    if not row:
        print(f"{R}Не нашёл '{label}'.{X}")
        return
    print(f"\n{Y}Удалить:{X}  {B}{row['label']}{X}  {D}({row['provider']}, {row['cli_home_path']}){X}")
    print(f"Удалить также папку с auth (вылогинит этот аккаунт)? [y/N] › ", end="", flush=True)
    also_dir = getch().lower()
    print(also_dir)
    confirm = input(f"{R}Подтверди удаление '{label}' [yes]:{X} ").strip().lower()
    if confirm != "yes":
        print("Отмена.")
        return
    with db() as conn:
        conn.execute("DELETE FROM accounts WHERE label=?", (label,))
        conn.commit()
    print(f"{G}✓ Запись удалена.{X}")
    if also_dir in ("y", "д"):
        try:
            shutil.rmtree(row["cli_home_path"])
            print(f"{G}✓ Папка удалена.{X}")
        except Exception as e:
            print(f"{R}✗ Не удалось удалить папку: {e}{X}")


def install_all():
    print(f"\n{B}{M}Установка зависимостей{X}\n")
    if not has_cmd("npm"):
        print(f"{R}✗ npm не найден. Поставь Node.js с https://nodejs.org{X}")
    else:
        for v in PROVIDERS.values():
            if has_cmd(v["bin"]):
                print(f"  {G}✓{X} {v['bin']} {D}уже установлен{X}")
            else:
                print(f"  {Y}↓{X} ставлю {v['npm_pkg']}...")
                npm_install(v["npm_pkg"])
    print(f"\n{B}Python-зависимости{X}")
    req = BASE_DIR / "requirements.txt"
    if req.exists():
        subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(req)])
    else:
        subprocess.call([sys.executable, "-m", "pip", "install", "aiogram"])


def edit_env():
    ensure_env()
    print(f"\n{D}Файл: {ENV_PATH}{X}")
    if os.name == "nt":
        subprocess.call(["notepad", str(ENV_PATH)])
    else:
        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, str(ENV_PATH)])


def start_bot():
    es = env_state()
    if not es["token_set"]:
        print(f"\n{R}✗ TELEGRAM_BOT_TOKEN не заполнен в .env.{X}")
        print(f"{D}Меню → 7 → вписать токен{X}")
        return
    rows = list_accounts()
    if not rows:
        print(f"\n{R}✗ Нет ни одного аккаунта. Сначала добавь через пункт 2.{X}")
        return
    not_logged = []
    for r in rows:
        p = next((v for v in PROVIDERS.values() if v["key"] == r["provider"]), None)
        if p:
            logged, _ = is_logged_in(p["key"], Path(r["cli_home_path"]))
            if not logged:
                not_logged.append(r["label"])
    if not_logged:
        print(f"\n{Y}⚠ Не залогинены: {', '.join(not_logged)}{X}")
        print(f"Запустить всё равно? [y/N] › ", end="", flush=True)
        ans = getch().lower()
        print(ans)
        if ans not in ("y", "д"):
            return
    print(f"\n{C}▶ Запускаю bot.py...{X}")
    if not es["admin_set"]:
        print(f"{Y}⚠ ADMIN_TELEGRAM_ID пустой — в консоли появится claim-ссылка.{X}")
        print(f"{Y}  Открой её в Telegram → Start → бот сам впишет твой id.{X}")
    print()
    subprocess.call([sys.executable, str(BASE_DIR / "bot.py")])


# ---------- главный цикл ----------
def settings_menu():
    """Подменю «Настройки»: редкие/служебные действия."""
    while True:
        header()
        print(f"\n  {B}{M}Настройки{X}")
        choice = render_menu(MENU_SETTINGS)
        print(choice)
        if choice == "1": login_existing()
        elif choice == "2": remove_account()
        elif choice == "3": show_disk_state()
        elif choice == "4": install_all()
        elif choice == "5": edit_env()
        elif choice in ("0", "\x1b", "q", "Q"):
            return
        else:
            print(f"{R}Не понял.{X}")
        press_any_key()


def main():
    db_init()
    ensure_env()
    while True:
        header()
        choice = render_menu(MENU_MAIN)
        print(choice)
        if choice == "1": show_accounts()
        elif choice == "2": add_account_interactive()
        elif choice == "3": show_history()
        elif choice == "8":
            start_bot()
            continue  # после остановки бота — сразу обратно в меню
        elif choice == "9":
            settings_menu()
            continue
        elif choice in ("0", "\x1b", "q", "Q"):
            print(f"\n{D}Пока.{X}")
            break
        else:
            print(f"{R}Не понял.{X}")
        press_any_key()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{D}Прервано.{X}")
