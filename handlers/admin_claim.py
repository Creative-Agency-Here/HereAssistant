"""Назначение админа через claim-код и отвязка (/logout)."""

import json
import logging
import secrets
import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from core import access, config, db, events
from .common import is_admin, is_allowed, send_long

router = Router()
log = logging.getLogger("bridge.claim")


def _persist_admin_id(uid: int):
    config.append_env("ADMIN_TELEGRAM_ID", str(uid))


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    # Если админ ещё не назначен — режим клейма (первый запуск)
    if config.ADMIN_ID is None:
        provided = (command.args or "").strip()
        if provided != config.CLAIM_CODE:
            await message.answer(
                "🔐 Бот запущен, но ещё не привязан к владельцу.\n\n"
                "Как привязать (1 минута):\n"
                "1. Открой консоль, где запущен бот\n"
                "    (python bot.py / меню manage.py / pm2 logs)\n"
                "2. Там напечатан claim-код и готовая ссылка\n"
                "3. Перейди по ссылке — или отправь сюда:\n"
                "    /start <код>\n\n"
                "Владельцем станет первый, кто пришлёт код."
            )
            return
        config.ADMIN_ID = message.from_user.id
        # И runtime-список тоже: is_owner смотрит в ADMIN_IDS, а .env
        # перечитается только при рестарте — без этого свежий владелец
        # оказался бы заблокирован до перезапуска.
        if config.ADMIN_ID not in config.ADMIN_IDS:
            config.ADMIN_IDS.append(config.ADMIN_ID)
        _persist_admin_id(config.ADMIN_ID)
        with db.conn() as c:
            # Upsert, не IGNORE: middleware уже вставил эту строку как
            # user/pending ДО хендлера — клейм обязан поднять роль.
            c.execute(
                "INSERT INTO users(telegram_id, username, role, status, created_at) "
                "VALUES (?, ?, 'admin', 'approved', ?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET role='admin', status='approved', "
                "  username=COALESCE(excluded.username, users.username)",
                (config.ADMIN_ID, message.from_user.username, int(time.time())),
            )
        events.log("admin_claim",
                   user_id=config.ADMIN_ID, chat_id=message.chat.id,
                   payload={"username": message.from_user.username})
        await message.answer(
            f"✅ Готово — ты владелец бота (id {config.ADMIN_ID} сохранён в .env).\n\n"
            "Быстрый старт:\n"
            "1. /accounts — подключи подписку CLI-агента (Claude / Codex / Gemini)\n"
            "2. /project — выбери рабочую папку\n"
            "3. Пиши задачу обычным текстом — агент выполнит\n\n"
            "Коллеги: пусть просто напишут боту — тебе придёт заявка\n"
            "с кнопками (режимы: /access, роли: /users).\n"
            "Терминальный чат: python chat.py. /help — вся справка."
        )
        log.info("Admin claimed by user_id=%s username=%s",
                 config.ADMIN_ID, message.from_user.username)
        return

    if not is_allowed(message):
        # незнакомец/ожидающий/отклонённый — ответ по режиму доступа:
        # заявка владельцу с кнопками, подсказка или отказ (handlers/team.py)
        from .team import handle_unauthorized
        await handle_unauthorized(message, message.bot)
        return

    extra = ("Команда: /users · режим доступа: /access\n"
             if is_admin(message) else "")
    await send_long(message,
        "Мульти-CLI мост готов. ✅ Ты авторизован.\n\n"
        "/accounts — подписки CLI-агентов · /project — рабочая папка\n"
        "/status — что активно · /logout — снять свой доступ · /help — справка\n"
        + extra +
        "\nПиши задачу обычным текстом — агент выполнит и покажет ход работы."
    )


@router.message(Command("logout"))
async def cmd_logout(message: Message, command: CommandObject):
    """Снять свой доступ. Пользователь/админ из БД — просто закрывается
    (вернёт владелец в /users). Последний владелец из .env → бот уходит в
    режим привязки: claim-код ротируется (старый из .env мог утечь), рестарт —
    через штатный restart_watcher (дождётся тишины, не оборвёт задачу)."""
    uid = message.from_user.id
    owner = access.is_owner(uid)
    if not owner and not is_allowed(message):
        await message.answer("Ты и так не авторизован. /start — как получить доступ.")
        return
    if (command.args or "").strip().lower() != "confirm":
        if owner:
            last = len(config.ADMIN_IDS) <= 1
            warn = ("⚠️ Ты последний владелец: бот перезапустится в режим привязки,\n"
                    "новый claim-код появится в консоли (старый перестанет работать).\n\n"
                    if last else "")
        else:
            warn = "Твой доступ закроется; вернуть его сможет владелец в /users.\n\n"
        await message.answer(
            "Отвязать твой аккаунт от бота?\n\n" + warn +
            "Подтверждение: /logout confirm"
        )
        return
    if not owner:
        access.deny(uid)
        events.log("logout", user_id=uid, chat_id=message.chat.id,
                   payload={"kind": "db-user"})
        await message.answer(
            "✅ Твой доступ снят.\n"
            "Вернуть может владелец: /users → выбрать тебя → «Открыть доступ»."
        )
        return
    config.remove_env_admin(uid)
    if uid in config.ADMIN_IDS:
        config.ADMIN_IDS.remove(uid)
    config.ADMIN_ID = config.ADMIN_IDS[0] if config.ADMIN_IDS else None
    # Отозвать и БД-роль: у владельца всегда есть строка admin/approved
    # (db.init сеет её) — без deny «отвязанный» сохранил бы полный доступ.
    access.deny(uid)
    events.log("logout", user_id=uid, chat_id=message.chat.id,
               payload={"admins_left": len(config.ADMIN_IDS)})
    if config.ADMIN_ID is None:
        new_code = secrets.token_urlsafe(8)
        config.append_env("CLAIM_CODE", new_code)
        config.CLAIM_CODE = new_code
        config.RESTART_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.RESTART_REQUEST_FILE.write_text(
            json.dumps({
                "chat_id": message.chat.id,
                "thread_id": message.message_thread_id or 0,
                "reason": "/logout — отвязка владельца",
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        await message.answer(
            "✅ Аккаунт отвязан — владельца больше нет.\n\n"
            "Бот перезапустится в режим привязки: новый claim-код\n"
            "появится в консоли (pm2 logs / manage.py).\n"
            "Новый владелец: /start <код>."
        )
    else:
        await message.answer(
            "✅ Твой доступ снят.\n"
            "Вернуть может оставшийся владелец: /users → «Открыть доступ»."
        )
    log.info("logout user_id=%s admins_left=%s", uid, config.ADMIN_IDS)
