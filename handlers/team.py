"""Команда и доступ: заявки на допуск, /users (роли кнопками), /access (режимы).

Всё живёт в БД (core/access.py) — .env больше не редактируется руками:
незнакомец пишет боту → карточка-заявка владельцу → ✅/👑/⛔ одним нажатием.
"""

import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core import access, events

from .common import is_admin

router = Router()
log = logging.getLogger("bridge.team")


# ---------- отказ/заявка для неавторизованных (зовут /start и messages) ----------
async def handle_unauthorized(message: Message, bot: Bot):
    """Единый ответ неавторизованному — по режиму доступа."""
    u = message.from_user
    if not u:
        return
    row = access.get_user(u.id) or access.upsert_seen(u.id, u.username, u.first_name)
    mode = access.get_mode()
    if row["status"] == "denied":
        await message.answer("⛔ Доступ отклонён владельцем бота.")
        return
    # (open-режим сюда не попадает: is_allowed_id пускает любого не-denied)
    if mode == "admins":
        await message.answer(
            "🔒 Бот работает в режиме «только админы».\n\n"
            f"Твой id: {u.id}\n"
            "Попроси владельца открыть тебе доступ — он сделает это\n"
            "прямо в боте: /users → выбрать тебя → назначить."
        )
        return
    # режим «по подтверждению»: шлём карточку-заявку один раз.
    # requested_at ставим ТОЛЬКО если карточка реально дошла хоть одному
    # админу — иначе юзер завис бы «на рассмотрении», которого никто не видел;
    # без отметки следующее сообщение повторит отправку.
    if not row["requested_at"]:
        sent = await notify_access_request(bot, row)
        events.log(
            "access_request",
            user_id=u.id,
            chat_id=message.chat.id,
            payload={"username": u.username, "notified": sent},
        )
        if sent:
            access.mark_requested(u.id)
            await message.answer(
                "📨 Заявка на доступ отправлена владельцу.\n"
                "Как только он подтвердит — бот заработает, я напишу."
            )
        else:
            log.warning("заявка от %s не доставлена ни одному админу", u.id)
            await message.answer("⚠️ Не смог достучаться до владельца — попробуй написать позже.")
    else:
        await message.answer("⏳ Твоя заявка ещё на рассмотрении — владелец увидит её.")


def _request_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Разрешить", callback_data=f"usr:approve:{uid}"),
                InlineKeyboardButton(text="👑 Сразу админом", callback_data=f"usr:promote:{uid}"),
            ],
            [InlineKeyboardButton(text="⛔ Отклонить", callback_data=f"usr:deny:{uid}")],
        ]
    )


async def notify_access_request(bot: Bot, row) -> int:
    """Карточка-заявка всем эффективным админам. Возвращает, скольким дошло."""
    text = f"🔔 Заявка на доступ к боту\n\n{access.user_line(row)}\n\nРазрешить работу с агентом?"
    sent = 0
    for admin_id in _admin_ids_all():
        try:
            await bot.send_message(admin_id, text, reply_markup=_request_kb(row["telegram_id"]))
            sent += 1
        except Exception:
            # админ мог не начинать чат с ботом — ему написать нельзя
            log.debug("не смог отправить заявку админу %s", admin_id, exc_info=True)
    return sent


def _admin_ids_all():
    """Владельцы из .env + назначенные админы из БД (без дублей)."""
    ids = []
    from core import config

    for uid in config.ADMIN_IDS:
        if uid not in ids:
            ids.append(uid)
    for u in access.list_users(limit=200):
        if u["role"] == "admin" and u["status"] == "approved" and u["telegram_id"] not in ids:
            ids.append(u["telegram_id"])
    return ids


# ---------- показ: edit_text с фолбэком ----------
async def _show(query: CallbackQuery, text: str, kb):
    """Отредактировать сообщение под колбэком. Карточка старше 48 ч приходит
    как InaccessibleMessage (без edit_text) — тогда шлём новое сообщение.
    «message is not modified» глотаем: это не ошибка, просто нет изменений."""
    msg = query.message
    if isinstance(msg, Message):
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            log.debug("edit_text не удался, шлю новое сообщение: %s", e)
    if msg:  # InaccessibleMessage: chat/message_id есть, редактировать нельзя
        await query.bot.send_message(msg.chat.id, text, reply_markup=kb)


# ---------- /users — кто писал боту, роли кнопками ----------
def _users_list_view(search: str = ""):
    total = access.count_users(search)
    rows = access.list_users(search, limit=10)  # pending — первыми (не тонут)
    if not rows:
        return (
            "Никого не нашёл"
            + (f" по «{search}»" if search else "")
            + ".\nЗдесь появляются все, кто писал боту."
        ), None
    head = ("Команда бота" if not search else f"Поиск: «{search}»") + f" · {total} чел."
    lines = [head + "\n👑 владелец · ⭐ админ · ✅ допущен · ⏳ заявка · ⛔ отказ\n"]
    buttons = []
    row_btns = []
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {access.user_line(r)}")
        row_btns.append(
            InlineKeyboardButton(text=str(i), callback_data=f"usr:card:{r['telegram_id']}")
        )
        if len(row_btns) == 5:
            buttons.append(row_btns)
            row_btns = []
    if row_btns:
        buttons.append(row_btns)
    if total > len(rows):
        lines.append(f"\n…показаны {len(rows)} из {total} — сузь поиском: /users <ник|имя|id>")
    else:
        lines.append("\nНомер — карточка с действиями. Поиск: /users <ник|имя|id>")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("users"))
async def cmd_users(message: Message, command: CommandObject):
    if not is_admin(message):
        return
    text, kb = _users_list_view((command.args or "").strip())
    await message.answer(text, reply_markup=kb)


def _card_view(uid: int):
    row = access.get_user(uid)
    if not row:
        return "Пользователь не найден.", None
    when = time.strftime("%d.%m.%Y %H:%M", time.localtime(row["last_seen"] or row["created_at"]))
    lines = [access.user_line(row), f"последняя активность: {when}"]
    btns = []
    if access.is_owner(uid):
        lines.append("владелец бота (бутстрап из .env) — роль не меняется")
    else:
        if row["status"] == "pending":
            btns.append(
                [
                    InlineKeyboardButton(text="✅ Разрешить", callback_data=f"usr:approve:{uid}"),
                    InlineKeyboardButton(
                        text="👑 Сразу админом", callback_data=f"usr:promote:{uid}"
                    ),
                ]
            )
            btns.append(
                [InlineKeyboardButton(text="⛔ Отклонить", callback_data=f"usr:deny:{uid}")]
            )
        elif row["status"] == "denied":
            btns.append(
                [InlineKeyboardButton(text="✅ Открыть доступ", callback_data=f"usr:approve:{uid}")]
            )
        elif row["role"] == "admin":
            btns.append(
                [
                    InlineKeyboardButton(text="⬇️ Снять админа", callback_data=f"usr:demote:{uid}"),
                    InlineKeyboardButton(text="⛔ Закрыть доступ", callback_data=f"usr:deny:{uid}"),
                ]
            )
        else:
            btns.append(
                [
                    InlineKeyboardButton(
                        text="👑 Назначить админом", callback_data=f"usr:promote:{uid}"
                    ),
                    InlineKeyboardButton(text="⛔ Закрыть доступ", callback_data=f"usr:deny:{uid}"),
                ]
            )
    btns.append([InlineKeyboardButton(text="← Список", callback_data="usr:list")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=btns)


# ---------- callbacks ----------
def _uid_from(data: str) -> int:
    return int(data.split(":")[-1])


async def _guard(query: CallbackQuery) -> bool:
    if not query.from_user or not access.is_admin_id(query.from_user.id):
        await query.answer("Доступ запрещён", show_alert=True)
        return False
    return True


@router.callback_query(F.data == "usr:list")
async def cb_users_list(query: CallbackQuery):
    if not await _guard(query):
        return
    text, kb = _users_list_view()
    await _show(query, text, kb)
    await query.answer()


@router.callback_query(F.data.startswith("usr:card:"))
async def cb_user_card(query: CallbackQuery):
    if not await _guard(query):
        return
    text, kb = _card_view(_uid_from(query.data))
    await _show(query, text, kb)
    await query.answer()


async def _apply_role_action(query: CallbackQuery, action: str):
    """Общий обработчик approve/deny/promote/demote: правка БД, карточка,
    уведомление самому пользователю."""
    if not await _guard(query):
        return
    uid = _uid_from(query.data)
    if access.is_owner(uid):
        await query.answer("Это владелец — роль фиксирована", show_alert=True)
        return
    if uid == query.from_user.id and action in ("promote", "deny", "demote"):
        await query.answer("Свою роль менять нельзя — попроси другого админа", show_alert=True)
        return
    row_before = access.get_user(uid)
    if not row_before:
        await query.answer("Пользователь не найден", show_alert=True)
        return
    do = {
        "approve": access.approve,
        "deny": access.deny,
        "promote": access.promote,
        "demote": access.demote,
    }[action]
    do(uid)
    events.log("access_" + action, user_id=query.from_user.id, payload={"target": uid})
    # уведомить пользователя о решении (если это была заявка/смена доступа)
    note = {
        "approve": "✅ Доступ к боту открыт — пиши задачу обычным текстом.",
        "promote": "👑 Тебе открыт доступ с правами админа (/users, /access).",
        "deny": "⛔ Доступ к боту закрыт владельцем.",
        "demote": None,  # снятие роли — без уведомления, доступ остаётся
    }[action]
    if note:
        try:
            await query.bot.send_message(uid, note)
        except Exception:
            log.debug("не смог уведомить пользователя %s", uid, exc_info=True)
    text, kb = _card_view(uid)
    await _show(query, text, kb)
    await query.answer("Готово")


@router.callback_query(F.data.startswith("usr:approve:"))
async def cb_user_approve(query: CallbackQuery):
    await _apply_role_action(query, "approve")


@router.callback_query(F.data.startswith("usr:deny:"))
async def cb_user_deny(query: CallbackQuery):
    await _apply_role_action(query, "deny")


@router.callback_query(F.data.startswith("usr:promote:"))
async def cb_user_promote(query: CallbackQuery):
    await _apply_role_action(query, "promote")


@router.callback_query(F.data.startswith("usr:demote:"))
async def cb_user_demote(query: CallbackQuery):
    await _apply_role_action(query, "demote")


# ---------- /access — режим доступа ----------
def _access_view():
    cur = access.get_mode()
    lines = ["Режим доступа — кто может пользоваться ботом:\n"]
    buttons = []
    for mode in access.MODES:
        mark = "✓ " if mode == cur else "   "
        lines.append(f"{mark}{access.MODE_TITLES[mode]}")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=("✓ " if mode == cur else "") + access.MODE_TITLES[mode].split(" — ")[0],
                    callback_data=f"axs:set:{mode}",
                )
            ]
        )
    lines.append("\nРоли и заявки: /users")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("access"))
async def cmd_access(message: Message):
    if not is_admin(message):
        return
    text, kb = _access_view()
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("axs:set:"))
async def cb_access_set(query: CallbackQuery):
    if not await _guard(query):
        return
    mode = query.data.split(":")[-1]
    if mode not in access.MODES:
        await query.answer("Неизвестный режим", show_alert=True)
        return
    if mode == access.get_mode():
        # без ранней отсечки edit_text упал бы «message is not modified»
        await query.answer("Этот режим уже включён")
        return
    access.set_mode(mode)
    events.log("access_mode", user_id=query.from_user.id, payload={"mode": mode})
    text, kb = _access_view()
    await _show(query, text, kb)
    await query.answer(f"Режим: {access.MODE_TITLES[mode].split(' — ')[0]}")
