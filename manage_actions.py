"""Interactive account/login/runtime actions manager UI."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from core import projects, rtk
from manage_accounts import (
    AccountExistsError,
    NewAccount,
    ProviderSpec,
    account_by_label,
    create_account,
    delete_account,
    list_accounts,
    sanitize_label,
    update_account_access,
)
from manage_config import BASE_DIR, CLI_HOMES_DIR, DB_PATH, ENV_PATH, PROVIDERS
from manage_env import admin_ids, ensure_env, env_state
from manage_process import (
    LOGIN_STATE_INACCESSIBLE,
    has_command,
    install_npm_package,
    login_state,
    run_visible,
)
from manage_ui import BG_G, BG_R, BLACK, B, C, D, G, M, R, W, X, Y, badge, getch, line
from manage_views import show_accounts as render_accounts


def has_cmd(name: str) -> bool:
    return has_command(name)


def npm_install(package: str) -> bool:
    if not has_command("npm"):
        print(f"{R}✗ npm не найден. Поставь Node.js с https://nodejs.org{X}")
        return False
    return install_npm_package(package)


def is_logged_in(provider_key: str, cli_home: Path) -> tuple[bool, str]:
    return login_state(provider_key, cli_home)


def show_accounts():
    return render_accounts(DB_PATH, PROVIDERS)


def find_account(label: str):
    return account_by_label(DB_PATH, label)


def pick_provider() -> ProviderSpec | None:
    print(f"\n{B}Выбери провайдера{X}  {D}(нажми 1/2/3, или Esc — отмена){X}")
    print(line())
    for k, v in PROVIDERS.items():
        ready = badge("OK ", BLACK, BG_G) if has_cmd(v["bin"]) else badge("нет CLI", W, BG_R)
        print(f"  {B}[{k}]{X}  {ready}  {B}{v['title']}{X} {D}{v['subtitle']}{X}")
    print("\n› ", end="", flush=True)
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
        print("Поставить через npm? [Y/n] › ", end="", flush=True)
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
    safe = sanitize_label(label)
    if safe != label:
        print(f"{Y}В label оставлены только буквы/цифры/-_: '{safe}'{X}")
        label = safe

    note = input(f"{B}Заметка{X} {D}(email или описание, можно пусто){X}: ").strip()
    model = (
        input(f"{B}Модель{X} {D}(Enter — '{prov['default_model']}'){X}: ").strip()
        or prov["default_model"]
    )

    # Пустой owner не означает shared: общий доступ подтверждается отдельно.
    ids = admin_ids(ENV_PATH)
    hint = f" {D}(админы: {', '.join(ids)}){X}" if ids else ""
    owner_raw = input(
        f"{B}Владелец{X} {D}(Telegram user_id; Enter — не назначен){X}{hint}: "
    ).strip()
    owner = int(owner_raw) if owner_raw.lstrip("-").isdigit() else None
    shared = False
    if owner is None:
        shared_raw = input(f"{B}Явно общий аккаунт?{X} {D}[y/N]{X}: ").strip().lower()
        shared = shared_raw in ("y", "yes", "д", "да")

    cli_home = CLI_HOMES_DIR / f"{prov['key']}__{label}"
    cli_home.mkdir(parents=True, exist_ok=True)

    try:
        create_account(
            DB_PATH,
            NewAccount(
                provider=prov["key"],
                label=label,
                cli_home_path=cli_home,
                default_model=model,
                notes=note,
                owner_user_id=owner,
                shared=shared,
            ),
        )
    except AccountExistsError:
        print(f"{R}Аккаунт с label='{label}' уже есть.{X}")
        return

    print(f"\n{G}✓ Аккаунт '{label}' зарегистрирован{X}")
    print(f"{D}папка: {cli_home}{X}")

    print("\nЗалогиниться сейчас? [Y/n] › ", end="", flush=True)
    ans = getch().lower()
    print(ans)
    if ans not in ("n", "т"):
        do_login(prov, cli_home)


def do_login(prov: ProviderSpec, cli_home: Path) -> None:
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
    if hint == LOGIN_STATE_INACCESSIBLE:
        print(f"\n{Y}⚠ Профиль защищён OS runner; status недоступен пользователю manager.{X}")
    elif logged:
        print(f"\n{G}✓ Логин зафиксирован ({hint}){X}")
        if prov["key"] == "claude_code" and rtk.configure_claude_profile(cli_home):
            print(f"{G}✓ RTK hook и безопасные read/test permissions подключены.{X}")
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
    row = find_account(label)
    if not row:
        print(f"{R}Не нашёл '{label}'.{X}")
        return
    prov = next((v for v in PROVIDERS.values() if v["key"] == row["provider"]), None)
    if not prov:
        print(f"{R}Неизвестный провайдер.{X}")
        return
    do_login(prov, Path(row["cli_home_path"]))


def configure_account_access():
    rows = show_accounts()
    if not rows:
        return
    label = input(f"\n{B}Label аккаунта{X} {D}(Enter — отмена){X}: ").strip()
    if not label or not find_account(label):
        print("Отмена." if not label else f"{R}Не нашёл '{label}'.{X}")
        return
    ids = admin_ids(ENV_PATH)
    hint = f" {D}(админы: {', '.join(ids)}){X}" if ids else ""
    owner_raw = input(
        f"{B}Новый владелец{X} {D}(Telegram user_id; Enter — без владельца){X}{hint}: "
    ).strip()
    owner = int(owner_raw) if owner_raw.lstrip("-").isdigit() else None
    shared = False
    if owner is None:
        answer = input(f"{B}Сделать явно shared?{X} {D}[y/N]{X}: ").strip().lower()
        shared = answer in ("y", "yes", "д", "да")
    if update_account_access(DB_PATH, label, owner_user_id=owner, shared=shared):
        mode = f"владелец {owner}" if owner is not None else ("shared" if shared else "закрыт")
        print(f"{G}✓ Доступ обновлён: {mode}.{X}")


def register_project_interactive():
    ids = admin_ids(ENV_PATH)
    hint = f" {D}(админы: {', '.join(ids)}){X}" if ids else ""
    owner_raw = input(f"\n{B}Владелец проекта{X} {D}(Telegram user_id){X}{hint}: ").strip()
    if not owner_raw.lstrip("-").isdigit():
        print(f"{R}Нужен числовой Telegram user_id.{X}")
        return
    name = input(f"{B}Имя проекта{X}: ").strip()
    safe = sanitize_label(name)
    if not safe or safe != name:
        print(f"{R}Имя должно содержать только буквы/цифры/-/_.{X}")
        return
    root = input(f"{B}Абсолютный root_path{X}: ").strip()
    try:
        project = projects.register_owned_project(int(owner_raw), name, root)
    except (OSError, ValueError, sqlite3.IntegrityError) as error:
        print(f"{R}Не удалось зарегистрировать проект: {error}{X}")
        return
    print(f"{G}✓ Проект {project['name']} зарегистрирован: {project['root_path']}{X}")


def remove_account():
    rows = show_accounts()
    if not rows:
        return
    label = input(f"\n{B}Label для удаления{X} {D}(Enter — отмена){X}: ").strip()
    if not label:
        return
    row = find_account(label)
    if not row:
        print(f"{R}Не нашёл '{label}'.{X}")
        return
    print(
        f"\n{Y}Удалить:{X}  {B}{row['label']}{X}  {D}({row['provider']}, {row['cli_home_path']}){X}"
    )
    print("Удалить также папку с auth (вылогинит этот аккаунт)? [y/N] › ", end="", flush=True)
    also_dir = getch().lower()
    print(also_dir)
    confirm = input(f"{R}Подтверди удаление '{label}' [yes]:{X} ").strip().lower()
    if confirm != "yes":
        print("Отмена.")
        return
    delete_account(DB_PATH, label)
    print(f"{G}✓ Запись удалена.{X}")
    if also_dir in ("y", "д"):
        try:
            shutil.rmtree(row["cli_home_path"])
            print(f"{G}✓ Папка удалена.{X}")
        except OSError as error:
            print(f"{R}✗ Не удалось удалить папку: {error}{X}")


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
    ensure_env(ENV_PATH)
    print(f"\n{D}Файл: {ENV_PATH}{X}")
    if os.name == "nt":
        subprocess.call(["notepad", str(ENV_PATH)])
    else:
        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, str(ENV_PATH)])


def start_bot():
    es = env_state(ENV_PATH)
    if not es["token_set"]:
        print(f"\n{R}✗ TELEGRAM_BOT_TOKEN не заполнен в .env.{X}")
        print(f"{D}Меню → 7 → вписать токен{X}")
        return
    rows = list_accounts(DB_PATH)
    if not rows:
        print(f"\n{R}✗ Нет ни одного аккаунта. Сначала добавь через пункт 2.{X}")
        return
    not_logged = []
    for r in rows:
        p = next((v for v in PROVIDERS.values() if v["key"] == r["provider"]), None)
        if p:
            logged, hint = is_logged_in(p["key"], Path(r["cli_home_path"]))
            if not logged and hint != LOGIN_STATE_INACCESSIBLE:
                not_logged.append(r["label"])
    if not_logged:
        print(f"\n{Y}⚠ Не залогинены: {', '.join(not_logged)}{X}")
        print("Запустить всё равно? [y/N] › ", end="", flush=True)
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
