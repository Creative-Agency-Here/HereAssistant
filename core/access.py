"""Система доступа: роли и режимы — в БД, не в .env.

Модель:
  • Владелец  — id в ADMIN_IDS (.env). Это только бутстрап (claim при первом
    запуске); дальше роли живут в БД и раздаются кнопками из бота.
  • Админ     — users.role='admin' (назначается владельцем/админом в /users).
  • Пользователь — users.status='approved': может работать с агентом.
  • pending   — написал боту, ждёт подтверждения; denied — отклонён.

Режимы доступа (settings.access_mode):
  open    — каждый написавший сразу допущен;
  approve — новичок создаёт заявку, владелец подтверждает кнопками (дефолт);
  admins  — работают только владельцы и назначенные админы.
"""

import time

from . import config, db

MODES = ("open", "approve", "admins")
DEFAULT_MODE = "approve"

MODE_TITLES = {
    "open": "🟢 Открытый — каждый написавший сразу допущен",
    "approve": "🟡 По подтверждению — новичок ждёт одобрения владельца",
    "admins": "🔴 Только админы — доступ лишь владельцам и назначенным",
}


# ---------- настройки ----------
def get_mode() -> str:
    with db.conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key='access_mode'").fetchone()
    return row["value"] if row and row["value"] in MODES else DEFAULT_MODE


def set_mode(mode: str):
    if mode not in MODES:
        raise ValueError(f"неизвестный режим: {mode}")
    with db.conn() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES ('access_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (mode,),
        )


# ---------- пользователи ----------
def get_user(uid: int):
    with db.conn() as c:
        return c.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()


def upsert_seen(uid: int, username=None, first_name=None):
    """Зафиксировать пишущего: новичок появляется в списке «кто писал» как
    pending, у знакомых обновляются ник/имя/last_seen. Статус НЕ зависит от
    режима: open-режим пускает не-denied «на лету» в is_allowed_id — поэтому
    переключение open→approve сразу возвращает новичков в очередь заявок,
    а не оставляет им вечный approved. Возвращает актуальную строку."""
    now = int(time.time())
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(telegram_id, username, first_name, role, status, created_at, last_seen) "
            "VALUES (?, ?, ?, 'user', 'pending', ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET "
            "  username=COALESCE(excluded.username, users.username), "
            "  first_name=COALESCE(excluded.first_name, users.first_name), "
            "  last_seen=excluded.last_seen",
            (uid, username, first_name, now, now),
        )
        return c.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()


def _search_where(search: str):
    if not search:
        return "", []
    like = f"%{search.lstrip('@')}%"
    return (
        " WHERE CAST(telegram_id AS TEXT) LIKE ? "
        "OR username LIKE ? COLLATE NOCASE "
        "OR first_name LIKE ? COLLATE NOCASE"
    ), [like, like, like]


def list_users(search: str = "", limit: int = 30):
    """Все, кто писал боту; поиск по нику/имени/id (LIKE, регистр не важен).
    Заявки (pending) — первыми, чтобы не тонули среди активных."""
    where, args = _search_where(search)
    q = (
        "SELECT * FROM users"
        + where
        + " ORDER BY (status='pending') DESC, COALESCE(last_seen, created_at) DESC LIMIT ?"
    )
    with db.conn() as c:
        return list(c.execute(q, args + [limit]))


def count_users(search: str = "") -> int:
    where, args = _search_where(search)
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM users" + where, args).fetchone()["n"]


# ---------- проверки ----------
def is_owner(uid) -> bool:
    return bool(uid) and uid in config.ADMIN_IDS


def is_admin_id(uid) -> bool:
    """Эффективный админ: владелец из .env ИЛИ назначенный в БД."""
    if is_owner(uid):
        return True
    row = get_user(uid) if uid else None
    return bool(row and row["role"] == "admin" and row["status"] == "approved")


def is_allowed_id(uid) -> bool:
    """Допущен к работе с агентом — по режиму доступа:
    open — любой не-denied из списка «кто писал»; approve — только approved;
    admins — только владельцы и назначенные админы. Denied закрыт везде."""
    if is_owner(uid):
        return True
    row = get_user(uid) if uid else None
    if not row or row["status"] == "denied":
        return False
    mode = get_mode()
    if mode == "open":
        return True
    if row["status"] != "approved":
        return False
    if mode == "admins":
        return row["role"] == "admin"
    return True


# ---------- действия над пользователями ----------
def _set(uid: int, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.conn() as c:
        c.execute(f"UPDATE users SET {cols} WHERE telegram_id=?", (*fields.values(), uid))


def approve(uid: int):
    _set(uid, status="approved")


def deny(uid: int):
    _set(uid, status="denied", role="user")


def promote(uid: int):
    _set(uid, status="approved", role="admin")


def demote(uid: int):
    _set(uid, role="user")


def mark_requested(uid: int):
    _set(uid, requested_at=int(time.time()))


def user_badge(row) -> str:
    """Эмодзи-статус для списков: 👑 владелец, ⭐ админ, ✅ юзер, ⏳/⛔."""
    if is_owner(row["telegram_id"]):
        return "👑"
    if row["role"] == "admin" and row["status"] == "approved":
        return "⭐"
    return {"approved": "✅", "pending": "⏳", "denied": "⛔"}.get(row["status"], "·")


def user_line(row) -> str:
    """Однострочка для списков: бейдж, имя, @ник, id."""
    name = row["first_name"] or ""
    nick = f"@{row['username']}" if row["username"] else ""
    label = " ".join(x for x in (name, nick) if x) or str(row["telegram_id"])
    return f"{user_badge(row)} {label} · {row['telegram_id']}"
