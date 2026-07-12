"""Конфиг: загрузка .env, пути, константы."""

import os
import secrets
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = BASE_DIR / ".runtime"
DOWNLOADS_DIR = RUNTIME_DIR / "downloads"
LOGS_DIR = RUNTIME_DIR / "logs"
BACKUPS_DIR = RUNTIME_DIR / "backups"
STATE_DIR = RUNTIME_DIR / "state"
CLI_HOMES_DIR = RUNTIME_DIR / "cli_homes"
WORKSPACE_DIR = BASE_DIR / "workspace"
DEFAULT_PROJECT_DIR = WORKSPACE_DIR / "default"

ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "bridge.sqlite3"
BOT_FILE = BASE_DIR / "bot.py"
TZ_FILE = BASE_DIR / "TZ.md"
RESTART_STATE_FILE = STATE_DIR / "restart.json"
# Запрос на перезапуск. Пишется /deploy или внешним кодом; единственный исполнитель
# самого execv — restart_watcher в bot.py (ждёт is_busy()==False, шлёт сигнал, потом рестарт).
RESTART_REQUEST_FILE = STATE_DIR / "restart_request.json"


def _load_env_file(path: Path):
    """Простой парсер .env: KEY=VALUE, # для комментариев."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def init_dirs():
    for d in (
        RUNTIME_DIR,
        DOWNLOADS_DIR,
        LOGS_DIR,
        BACKUPS_DIR,
        STATE_DIR,
        CLI_HOMES_DIR,
        WORKSPACE_DIR,
        DEFAULT_PROJECT_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def user_workspace(user_id: int) -> Path:
    """Личный workspace пользователя: workspace/<user_id>/ — проекты разных
    людей (Паша/Илья) физически разделены, агент ходит только в свою папку."""
    return WORKSPACE_DIR / str(int(user_id))


def user_default_cwd(user_id: int) -> str:
    """Дефолтная рабочая папка нового диалога пользователя (его личный default)."""
    d = user_workspace(user_id) / "default"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def append_env(key: str, value: str):
    """Дописать или обновить ключ в .env."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text("", encoding="utf-8")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    new_lines = []
    replaced = False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def remove_env_admin(uid: int):
    """Убрать id из ADMIN_IDS / ADMIN_TELEGRAM_ID в .env (для /logout).
    Пустой список после удаления — строка выкидывается целиком."""
    if not ENV_PATH.exists():
        return
    out = []
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("ADMIN_IDS=") or s.startswith("ADMIN_TELEGRAM_ID="):
            key, _, val = s.partition("=")
            ids = [x.strip() for x in val.split(",") if x.strip() and x.strip() != str(uid)]
            if ids:
                out.append(f"{key}={','.join(ids)}")
        else:
            out.append(line)
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------- загрузка ----------
_load_env_file(ENV_PATH)

TELEGRAM_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _parse_admin_ids(raw: str) -> list[int]:
    """Список Telegram-ID из строки через запятую (PASTE_HERE/мусор отбрасываем)."""
    out: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        p = part.strip()
        if p and p != "PASTE_HERE" and p.lstrip("-").isdigit():
            out.append(int(p))
    return out


# Основной способ — ADMIN_IDS (список); ADMIN_TELEGRAM_ID — легаси (один админ).
ADMIN_IDS: list[int] = _parse_admin_ids(os.environ.get("ADMIN_IDS", ""))
if not ADMIN_IDS:
    ADMIN_IDS = _parse_admin_ids(os.environ.get("ADMIN_TELEGRAM_ID", ""))
# Первый в списке — «главный» админ (обратная совместимость с ADMIN_ID).
ADMIN_ID: Optional[int] = ADMIN_IDS[0] if ADMIN_IDS else None

CLAIM_CODE: str = os.environ.get("CLAIM_CODE", "").strip()
if not ADMIN_IDS and not CLAIM_CODE:
    CLAIM_CODE = secrets.token_urlsafe(8)
    append_env("CLAIM_CODE", CLAIM_CODE)
    os.environ["CLAIM_CODE"] = CLAIM_CODE

DEFAULT_CWD: str = os.environ.get("DEFAULT_CWD", "").strip() or str(DEFAULT_PROJECT_DIR)
CLI_TIMEOUT: int = int(os.environ.get("CLI_TIMEOUT_SEC", "1800"))
GIT_ALLOWED_HOSTS: tuple[str, ...] = tuple(
    host.strip().lower()
    for host in os.environ.get("GIT_ALLOWED_HOSTS", "github.com,gitlab.com").split(",")
    if host.strip()
)
MAX_HISTORY: int = int(os.environ.get("MAX_HISTORY", "20"))
LOG_RETENTION_DAYS: int = int(os.environ.get("LOG_RETENTION_DAYS", "30"))
BACKUP_RETENTION_COUNT: int = int(os.environ.get("BACKUP_RETENTION_COUNT", "20"))

# URL веб-приложения (Telegram Mini App). Для /web и menu-кнопки.
# Должен быть https с валидным сертификатом — иначе Telegram кнопку не примет.
# Пустой = WebApp-кнопка не показывается (задай WEBAPP_URL в .env).
WEBAPP_URL: str = os.environ.get("WEBAPP_URL", "").strip()

# Секретный ключ доступа к вебапу из браузера/десктопа (где нет Telegram initData).
# Открыть ?key=<этот ключ> один раз — фронт запомнит и будет слать на каждый запрос.
WEBAPP_ACCESS_KEY: str = os.environ.get("WEBAPP_ACCESS_KEY", "").strip()


def webapp_url(path: str = "/", *, include_access_key: bool = False) -> str:
    if not WEBAPP_URL:
        return ""
    url = WEBAPP_URL.rstrip("/") + "/" + path.lstrip("/")
    if include_access_key and WEBAPP_ACCESS_KEY:
        separator = "&" if "?" in url else "?"
        url += f"{separator}key={WEBAPP_ACCESS_KEY}"
    return url


# Версия приложения (для /health, /api/status и баннера терминального чата).
APP_VERSION = "0.4.0"

# Токен сервисного API (/api/v1/*) для внешних систем (например, CRM).
# ПУСТОЙ по умолчанию = сервисные эндпоинты отключены (503), а не открыты.
# Токен НЕ обходит privacy-политику проектов: private/local невидимы всегда.
SERVICE_API_TOKEN: str = os.environ.get("SERVICE_API_TOKEN", "").strip()

RU_SYSTEM_INSTRUCTION = (
    "Ты — личный ассистент пользователя через Telegram-бот. "
    "Отвечай на русском языке. "
    "Будь краток, по делу, не повторяйся. "
    "Имена файлов, команды, переменные оставляй как есть. "
    "Можешь использовать Markdown — он корректно рендерится в Telegram: "
    "**жирный**, *курсив*, `inline-код`, ```блоки кода```, > цитаты, [ссылки](url), "
    "заголовки # ##, списки с -. "
    "Не злоупотребляй разметкой — используй там, где она реально помогает читаемости."
)


def env_state() -> dict:
    """Статус заполненности .env — для UI."""
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
        k = k.strip()
        v = v.strip()
        if k == "TELEGRAM_BOT_TOKEN":
            out["token_set"] = bool(v) and v != "PASTE_HERE"
        elif k in ("ADMIN_IDS", "ADMIN_TELEGRAM_ID"):
            if _parse_admin_ids(v):
                out["admin_set"] = True
        elif k == "CLAIM_CODE":
            out["claim_pending"] = bool(v)
    return out
