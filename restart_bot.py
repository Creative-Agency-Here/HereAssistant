"""Запрос перезапуска бота с уведомлением в Telegram.

Использование:
    python restart_bot.py [причина]

Что делает:
  1. Читает .env, находит токен и последний активный chat/thread в bridge.sqlite3
  2. Шлёт в Telegram "🔄 Перезапуск запланирован — <причина>"
  3. Записывает .runtime/state/restart_request.json — бот сам подхватит
     этот флаг и перезапустится ПОСЛЕ завершения текущей сессии,
     не обрывая ответ пользователю.

Бот не убивается — он сам делает os.execv когда становится свободен.
"""

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from urllib import parse, request

# Windows консоль по умолчанию cp1252 — не умеет русский. Принудим utf-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
DB_PATH = HERE / "bridge.sqlite3"
REQUEST_FILE = HERE / ".runtime" / "state" / "restart_request.json"


def read_env() -> dict:
    env = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_last_chat(env: dict) -> tuple[int, int]:
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT chat_id, thread_id FROM conversations "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                return row["chat_id"], row["thread_id"] or 0
        except Exception as e:
            print("warn: db read failed:", e)
    admin = env.get("ADMIN_TELEGRAM_ID", "0").strip()
    return (int(admin) if admin.lstrip("-").isdigit() else 0), 0


def send_telegram(token: str, chat_id: int, thread_id: int, text: str) -> None:
    if not token or not chat_id:
        print("warn: no token or chat_id, skip telegram")
        return
    data = {"chat_id": str(chat_id), "text": text}
    if thread_id:
        data["message_thread_id"] = str(thread_id)
    body = parse.urlencode(data).encode("utf-8")
    req = request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    request.urlopen(req, timeout=10).read()


def main():
    args = sys.argv[1:]
    # На случай если в скрипт по-прежнему передают PID — игнорируем его (легаси).
    if args and args[0].isdigit():
        args = args[1:]
    reason = " ".join(args).strip() or "обновление кода/конфига"

    env = read_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id, thread_id = get_last_chat(env)

    print(f"schedule restart chat={chat_id} thread={thread_id} reason={reason!r}")

    # Уведомление отправит сам бот через aiogram прямо перед os.execv —
    # urllib здесь может упасть на SSL (MITM-сертификаты).
    payload = {
        "requested_at": time.time(),
        "chat_id": chat_id,
        "thread_id": thread_id,
        "reason": reason,
    }
    REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    REQUEST_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("done — flag written, bot will self-restart")


if __name__ == "__main__":
    main()
