"""HereAssistant — Telegram-бот, мост к CLI-ассистентам.

Точка входа. Логика разнесена по модулям:
    core/       — конфиг, БД, логи, события, версия
    providers/  — обёртки над CLI (Claude Code, Codex, Gemini)
    handlers/   — Telegram-роутеры (один файл = одна группа команд)
    utils/      — markdown, скачивание файлов, локи
"""

import asyncio
import json
import logging
import os
import time

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from core import access, config, db, logging_setup, version
from handlers import ALL_ROUTERS
from handlers.deploy import post_restart_report, startup_notification
from utils.memory_link import ensure_memory_links
from utils.single_instance import ensure_single_instance

RESTART_REQUEST_FILE = config.RESTART_REQUEST_FILE


class UserSeen(BaseMiddleware):
    """Фиксирует каждого пишущего в users (для /users — «все, кто писал»):
    новичок появляется pending (или approved в open-режиме), у знакомых
    обновляются @username / имя / last_seen. Доступ этим НЕ выдаётся —
    его решают is_allowed/is_admin по статусу и режиму (core/access.py).
    Ник виден в терминальном чате (chat.py, «кто») и в атрибуции сессий."""

    async def __call__(self, handler, event, data):
        u = getattr(event, "from_user", None)
        if u:
            try:
                access.upsert_seen(u.id, u.username, u.first_name)
            except Exception:
                logging.getLogger("bridge").debug("user seen failed", exc_info=True)
        return await handler(event, data)


async def restart_watcher(bot: Bot):
    """Следит за .runtime/state/restart_request.json.

    Логика: рестартим ТОЛЬКО когда бот достаточно долго не занят — это значит
    финальные сообщения уже отправлены, а не оборвутся посередине.

      RESTART_QUIET_SEC      — сколько секунд подряд is_busy() должен быть False
      RESTART_MAX_WAIT_SEC   — потолок ожидания, даже если задача очень долгая
      RESTART_FLUSH_SEC      — пауза после отправки уведомления, чтобы Telegram точно его получил
    """
    from handlers.messages import is_busy

    log = logging.getLogger("bridge.restart")

    QUIET_SEC = float(os.environ.get("RESTART_QUIET_SEC", "5.0"))
    MAX_WAIT_SEC = float(os.environ.get("RESTART_MAX_WAIT_SEC", "600.0"))  # 10 мин
    FLUSH_SEC = float(os.environ.get("RESTART_FLUSH_SEC", "2.0"))
    POLL_SEC = 1.0

    while True:
        await asyncio.sleep(POLL_SEC)
        if not RESTART_REQUEST_FILE.exists():
            continue

        try:
            req = json.loads(RESTART_REQUEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            RESTART_REQUEST_FILE.unlink(missing_ok=True)
            continue

        # --- ждём пока бот станет «достаточно долго» свободен ---
        wait_started = time.time()
        quiet_since: float | None = None
        announced_wait = False
        while True:
            if is_busy():
                quiet_since = None
                if not announced_wait and time.time() - wait_started > 10:
                    log.info("restart: waiting for active task to finish…")
                    announced_wait = True
            else:
                if quiet_since is None:
                    quiet_since = time.time()
                elif time.time() - quiet_since >= QUIET_SEC:
                    break  # бот тих уже QUIET_SEC подряд
            if time.time() - wait_started > MAX_WAIT_SEC:
                log.warning("restart: max wait %.0fs exceeded, restarting anyway", MAX_WAIT_SEC)
                break
            await asyncio.sleep(POLL_SEC)

        chat_id = req.get("chat_id")
        thread_id = req.get("thread_id") or 0
        reason = req.get("reason", "обновление")

        # уведомление + ОБЯЗАТЕЛЬНАЯ пауза, чтобы Telegram точно успел доставить
        try:
            if chat_id:
                await bot.send_message(
                    chat_id,
                    f"🔄 Перезапускаю — {reason}",
                    message_thread_id=thread_id or None,
                )
        except Exception as e:
            log.warning("restart notify failed: %s", e)

        # Если /deploy уже записал детальный state (с backup/hash/diff) — НЕ затираем,
        # чтобы post_restart_report показал полный отчёт. Иначе пишем базовый.
        if not config.RESTART_STATE_FILE.exists():
            state = {
                "timestamp_before": time.time(),
                "chat_id": chat_id,
                "thread_id": thread_id,
                "hash_before": "",
                "text_before": "",
                "reason": reason,
            }
            config.RESTART_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            config.RESTART_STATE_FILE.write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
        try:
            RESTART_REQUEST_FILE.unlink()
        except Exception:
            pass
        log.info("self-restart fired (reason=%s) → exit(42), PM2 поднимет заново", reason)
        await asyncio.sleep(FLUSH_SEC)
        # НЕ os.execv: на Windows execv плодит НОВЫЙ PID, PM2 теряет процесс и флапает
        # (новый упирается в single-instance lock осиротевшего бота). Вместо этого
        # выходим с ненулевым кодом — PM2 (autorestart, stop_exit_codes=[0]) поднимет
        # тот же слот заново, чисто, с новым кодом и без сирот.
        os._exit(42)


COMMANDS = [
    BotCommand(command="help", description="Справка по командам"),
    BotCommand(command="accounts", description="Список аккаунтов"),
    BotCommand(command="account", description="Переключить аккаунт"),
    BotCommand(command="model", description="Сменить модель"),
    BotCommand(command="cwd", description="Сменить рабочую папку"),
    BotCommand(command="where", description="Текущая папка и проект"),
    BotCommand(command="project", description="Управление проектами в workspace/"),
    BotCommand(command="new", description="Новая сессия CLI"),
    BotCommand(command="reset", description="Очистить историю чата"),
    BotCommand(command="delete", description="Удалить беседу (БД + топик)"),
    BotCommand(command="status", description="Что сейчас активно"),
    BotCommand(command="stats", description="Статистика использования"),
    BotCommand(command="rtk", description="Экономия токенов RTK"),
    BotCommand(command="log", description="Последние события"),
    BotCommand(command="version", description="Хеш и дата bot.py"),
    BotCommand(command="deploy", description="Перезапустить процесс"),
    BotCommand(command="diff", description="Правки последнего ответа"),
    BotCommand(command="web", description="Открыть веб-интерфейс (Mini App)"),
    BotCommand(command="users", description="Команда: кто писал боту, роли (админ)"),
    BotCommand(command="access", description="Режим доступа к боту (админ)"),
    BotCommand(command="logout", description="Снять свой доступ / отвязать бота"),
]


async def main():
    log = logging_setup.setup()
    ensure_single_instance()  # выйдет с понятным сообщением, если уже запущен
    db.init()

    # Объединить auto-memory всех CLI-аккаунтов в HereAssistant\memory через junction
    try:
        stats = ensure_memory_links()
        if any(stats.values()):
            log.info("memory links: %s", stats)
    except Exception as e:
        log.warning("ensure_memory_links failed: %s", e)

    if not config.TELEGRAM_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN не задан в .env. Останов.")
        return

    bot = Bot(config.TELEGRAM_TOKEN)
    dp = Dispatcher()
    dp.message.middleware(UserSeen())
    for router in ALL_ROUTERS:
        dp.include_router(router)

    # автокомплит команд при наборе /
    try:
        await bot.set_my_commands(COMMANDS)
    except Exception as e:
        log.warning("set_my_commands не сработал: %s", e)

    # menu-кнопка (≡ слева от поля ввода) — открыть веб-приложение (Mini App)
    try:
        if config.WEBAPP_URL:
            _menu_url = config.WEBAPP_URL + (
                f"/?key={config.WEBAPP_ACCESS_KEY}" if config.WEBAPP_ACCESS_KEY else ""
            )
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Ассистент",
                    web_app=WebAppInfo(url=_menu_url),
                )
            )
    except Exception as e:
        log.warning("set_chat_menu_button не сработал: %s", e)

    # если бот стартует после /deploy — детальный отчёт (читает старый снимок)
    reported = False
    try:
        reported = await post_restart_report(bot)
    except Exception as e:
        log.warning("post_restart_report не сработал: %s", e)
    # иначе — короткое «✓ Бот запущен» (всегда при любом старте)
    if not reported:
        try:
            await startup_notification(bot)
        except Exception as e:
            log.warning("startup_notification не сработал: %s", e)

    # обновить снимок проекта — для следующего перезапуска
    try:
        version.save_snapshot()
    except Exception as e:
        log.warning("save_snapshot failed: %s", e)

    # фоновая таска для отложенного self-restart
    asyncio.create_task(restart_watcher(bot))

    if config.ADMIN_ID is None:
        try:
            me = await bot.get_me()
            link = f"https://t.me/{me.username}?start={config.CLAIM_CODE}"
        except Exception:
            link = f"/start {config.CLAIM_CODE}"
        print("\n" + "=" * 60)
        print("  АДМИН НЕ НАЗНАЧЕН")
        print("=" * 60)
        print(f"  Claim-код: {config.CLAIM_CODE}")
        print(f"  Ссылка:    {link}")
        print()
        print("  Открой ссылку в Telegram и нажми Start.")
        print("=" * 60 + "\n")
        log.info("Bot started in CLAIM mode. code=%s", config.CLAIM_CODE)
    else:
        log.info("Bot started. Admin=%s, DB=%s", config.ADMIN_ID, config.DB_PATH)

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("bridge").info("Остановлено по Ctrl+C")
