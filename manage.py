"""
HereAssistant — единый менеджер.

Запуск:
    python manage.py
"""

import sqlite3
import sys
from pathlib import Path

from manage_accounts import list_accounts as read_accounts
from manage_actions import (
    add_account_interactive,
    configure_account_access,
    edit_env,
    install_all,
    login_existing,
    register_project_interactive,
    remove_account,
    start_bot,
)
from manage_config import BASE_DIR, CLI_HOMES_DIR, DB_PATH, ENV_PATH, PROVIDERS, RUNTIME_DIR
from manage_env import ensure_env as ensure_env_file
from manage_env import env_state as read_env_state
from manage_env import env_template as build_env_template
from manage_header import render_header
from manage_process import bot_process_state, has_command, install_npm_package, login_state
from manage_process import run_visible as run_visible_process
from manage_ui import (
    B,
    C,
    D,
    G,
    M,
    R,
    X,
    Y,
    press_any_key,
    render_menu,
)
from manage_views import show_accounts as render_accounts
from manage_views import show_disk_state as render_disk_state
from manage_views import show_history as render_history

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
    owner_user_id INTEGER,
    shared INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1))
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL DEFAULT 0,
    account_id INTEGER, model TEXT, provider_session_id TEXT, cwd TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    UNIQUE (chat_id, thread_id)
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id INTEGER NOT NULL,
    name TEXT NOT NULL, root_path TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'private', enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    UNIQUE(owner_user_id, name), UNIQUE(root_path)
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'developer', created_at INTEGER NOT NULL,
    PRIMARY KEY(project_id, user_id)
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
        if "shared" not in cols:
            conn.execute(
                "ALTER TABLE accounts ADD COLUMN shared INTEGER NOT NULL DEFAULT 0 "
                "CHECK (shared IN (0, 1))"
            )
        conn.commit()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def has_cmd(name: str) -> bool:
    return has_command(name)


def run_visible(argv: list[str], env_extra: dict[str, str] | None = None) -> int:
    return run_visible_process(argv, env_extra)


def npm_install(pkg: str) -> bool:
    if not has_command("npm"):
        print(f"{R}✗ npm не найден. Поставь Node.js с https://nodejs.org{X}")
        return False
    return install_npm_package(pkg)


# ---------- .env ----------
def env_template() -> str:
    return build_env_template()


def ensure_env():
    ensure_env_file(ENV_PATH)


def env_state():
    return read_env_state(ENV_PATH)


# ---------- аккаунты ----------
def list_accounts() -> list[sqlite3.Row]:
    return read_accounts(DB_PATH)


def is_logged_in(provider_key: str, cli_home: Path) -> tuple[bool, str]:
    return login_state(provider_key, cli_home)


# ---------- инфо для шапки ----------


def header():
    render_header(base_dir=BASE_DIR, env_path=ENV_PATH, db_path=DB_PATH)


# Главное меню — только частое. Редкое спрятано в «Настройки».
# (клавиша, иконка+цвет, название, подсказка-тултип)
MENU_MAIN_BASE = [
    ("1", f"{C}▸{X}", "Аккаунты", "список подключённых CLI-аккаунтов и вход"),
    ("2", f"{G}+{X}", "Добавить аккаунт", "подключить и залогинить Claude / Codex / Gemini"),
    ("3", f"{C}≡{X}", "История · Аудит", "все обращения в Telegram и SSH-заходы по IP"),
    ("4", f"{C}❯{X}", "Чат в терминале", "интерактивный чат с агентом прямо здесь (как claude)"),
    ("9", f"{M}⚙{X}", "Настройки", "перелогин, удаление, зависимости, .env"),
    ("0", f"{D}⏻{X}", "Выход", ""),
]


def main_menu():
    process = bot_process_state(RUNTIME_DIR / "state" / "bot.lock")
    if process.running:
        bot_item = (
            "8",
            f"{G}●{X}",
            "Бот работает",
            f"PID {process.pid}; показать состояние",
        )
    else:
        bot_item = ("8", f"{G}▶{X}", "Запустить бота", "поднять бота (Ctrl+C — остановить)")
    return [*MENU_MAIN_BASE[:4], bot_item, *MENU_MAIN_BASE[4:]]


MENU_SETTINGS = [
    ("1", f"{Y}↻{X}", "Перелогиниться", "обновить вход существующего аккаунта"),
    ("2", f"{R}×{X}", "Удалить аккаунт", "отключить аккаунт и стереть его вход"),
    ("3", f"{C}▦{X}", "Файлы аккаунтов", "что лежит в .runtime/cli_homes"),
    ("4", f"{M}⚙{X}", "Зависимости", "поставить/обновить pip + npm"),
    ("5", f"{C}✎{X}", "Открыть .env", "токен бота, админы, настройки"),
    ("6", f"{C}⚿{X}", "Доступ аккаунта", "назначить владельца или явно shared"),
    ("7", f"{C}⌂{X}", "Зарегистрировать проект", "доверенный root для конкретного пользователя"),
    ("0", f"{D}←{X}", "Назад", ""),
]


# ---------- действия ----------
def show_history():
    render_history(DB_PATH)


def show_accounts():
    return render_accounts(DB_PATH, PROVIDERS)


def show_disk_state():
    render_disk_state(CLI_HOMES_DIR, DB_PATH)


# ---------- главный цикл ----------
def settings_menu():
    """Подменю «Настройки»: редкие/служебные действия."""
    while True:
        header()
        print(f"\n  {B}{M}Настройки{X}")
        choice = render_menu(MENU_SETTINGS)
        print(choice)
        if choice == "1":
            login_existing()
        elif choice == "2":
            remove_account()
        elif choice == "3":
            show_disk_state()
        elif choice == "4":
            install_all()
        elif choice == "5":
            edit_env()
        elif choice == "6":
            configure_account_access()
        elif choice == "7":
            register_project_interactive()
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
        choice = render_menu(main_menu())
        print(choice)
        if choice == "1":
            show_accounts()
        elif choice == "2":
            add_account_interactive()
        elif choice == "3":
            show_history()
        elif choice == "4":
            # Терминальный чат — отдельным процессом (свой asyncio-REPL).
            # НЕ continue: после выхода/ошибки чата делаем паузу (press_any_key
            # ниже), иначе сообщение об ошибке стёрлось бы перерисовкой меню.
            import subprocess

            subprocess.run([sys.executable, str(BASE_DIR / "chat.py")])
        elif choice == "8":
            start_bot()
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
