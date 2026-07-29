"""Команда /rc в чате бота: куда уходят сообщения этого треда.

Режим виден только владельцу (``config.ADMIN_ID``). Для остальных команда
молчит, а развилка в ``handlers/messages.py`` не срабатывает вовсе: удалённое
исполнение кода на компьютере владельца — не то, что раздают по ролям.

Цель выбирается ЯВНО и по ПУБЛИКАЦИИ, а не по компьютеру: одна машина держит
несколько проектов одновременно, и «самая свежая по умолчанию» однажды отправит
запрос не в тот проект, а отменить это нельзя.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core import config, herecrm_client, remote_bridge
from core.herecrm_client import HereCrmClientError

from . import repo

router = Router()
log = logging.getLogger("bridge.rc")

# В callback_data уезжает id ПУБЛИКАЦИИ: 7 байт префикса + uuid 36 = 43 из 64.
_PICK_PREFIX = "rc:use:"

_HELP = (
    "Удалённый режим /rc\n"
    "/rc — выбрать сессию компьютера, в которой будут исполняться сообщения треда\n"
    "/rc status — куда уходят сообщения сейчас\n"
    "/rc stop — остановить текущий запуск на устройстве\n"
    "/rc off — вернуться к обычной серверной сессии бота"
)


def _is_owner(user_id: int | None) -> bool:
    return config.ADMIN_ID is not None and user_id == config.ADMIN_ID


@router.message(Command("rc"))
async def cmd_rc(message: Message, command: CommandObject) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    argument = (command.args or "").strip().lower()
    if argument in ("off", "close", "выкл"):
        await _detach(message)
        return
    if argument in ("status", "st"):
        await _status(message)
        return
    if argument == "stop":
        await _stop(message)
        return
    if argument:
        await message.answer(_HELP)
        return
    await _picker(message)


@router.callback_query(F.data.startswith(_PICK_PREFIX))
async def pick_device(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id if callback.from_user else None):
        await _ack(callback, "Недоступно")
        return
    target = callback.message
    if not isinstance(target, Message):
        # callback.message может быть InaccessibleMessage: отвечать в него нечем.
        await _ack(callback, "Сообщение недоступно")
        return
    publication_id = (callback.data or "")[len(_PICK_PREFIX) :]
    await _ack(callback, await bind_publication(target, callback.from_user.id, publication_id))


async def bind_publication(target: Message, user_id: int, publication_id: str) -> str:
    """Привязывает тред к выбранной публикации. Возвращает текст подтверждения."""
    publications = await _load(target)
    if publications is None:
        return "Сервер недоступен"

    now = time.time()
    publication = remote_bridge.find_publication(publications, publication_id)
    if publication is None or not remote_bridge.is_live(publication, now):
        # Публикация могла закрыться между показом кнопок и нажатием.
        await _answer(target, remote_bridge.refusal_text(remote_bridge.SESSION_MOVED))
        return "Публикация уже недоступна"

    conv = _conversation(target, user_id)
    try:
        repo.set_remote_device(
            int(conv["id"]),
            publication.device_id,
            publication.device_name,
            publication.id,
            publication.conversation_id,
        )
    except (sqlite3.Error, ValueError, TypeError) as error:
        log.warning("не удалось привязать сессию устройства (%s)", type(error).__name__)
        return "Не удалось сохранить выбор"

    lines = [
        f"💻 Сообщения этого треда теперь исполняет {publication.device_name}.",
        remote_bridge.format_device_line(publication, now),
        f"Разрешено: {remote_bridge.capabilities_line(publication)}",
    ]
    if not publication.conversation_id:
        # Без сессии CRM цель определяется только устройством: пока у машины одна
        # живая публикация это работает, но текст ответа в чат не придёт.
        lines.append(
            "⚠️ Публикация не связана с сессией HereCRM: текста ответа в Telegram "
            "не будет, только статус. Если на компьютере появится вторая живая "
            "сессия, выбор придётся сделать заново."
        )
    lines.append("")
    lines.append("Вернуться к серверной сессии бота — /rc off.")
    await _answer(target, "\n".join(lines))
    return "Готово"


async def _picker(message: Message) -> None:
    publications = await _load(message)
    if publications is None:
        return
    now = time.time()
    targets = remote_bridge.live_publications(publications, now)
    if not targets:
        await message.answer(remote_bridge.refusal_text(remote_bridge.NO_PUBLICATION))
        return

    conv = _conversation(message, message.from_user.id if message.from_user else 0)
    binding = remote_bridge.conversation_binding(conv)
    rows = [
        [
            InlineKeyboardButton(
                text=f"💻 {remote_bridge.format_device_line(item, now)}",
                callback_data=f"{_PICK_PREFIX}{item.id}",
            )
        ]
        for item in targets
    ]
    header = (
        f"Сейчас тред исполняет {binding.label}."
        if binding.active
        else "Сейчас тред исполняет серверная сессия бота."
    )
    await message.answer(
        f"{header}\n\nВыбери сессию, куда уйдут следующие сообщения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _status(message: Message) -> None:
    conv = _conversation(message, message.from_user.id if message.from_user else 0)
    binding = remote_bridge.conversation_binding(conv)
    if not binding.active:
        await message.answer(
            "Удалённый режим выключен: сообщения исполняет серверная сессия бота.\nВключить — /rc."
        )
        return

    publications = await _load(message)
    if publications is None:
        return
    now = time.time()
    selection = remote_bridge.select_target(
        publications,
        binding.device_id,
        now=now,
        conversation_id=binding.conversation_id,
    )
    if selection.publication is None:
        await message.answer(
            f"💻 Привязка: {binding.label}\n"
            f"{remote_bridge.refusal_text(selection.refusal or 'rc_unavailable')}"
        )
        return
    publication = selection.publication
    lines = [
        f"💻 Привязка: {publication.device_name}",
        remote_bridge.format_device_line(publication, now),
        f"Приватность проекта: {publication.privacy_mode or 'не сообщена'}",
        f"Разрешено: {remote_bridge.capabilities_line(publication)}",
    ]
    if publication.device_platform:
        lines.append(f"Платформа: {publication.device_platform}")
    await message.answer("\n".join(lines))


async def _stop(message: Message) -> None:
    conv = _conversation(message, message.from_user.id if message.from_user else 0)
    binding = remote_bridge.conversation_binding(conv)
    if not binding.active:
        # Останавливать нечего: тред исполняет серверная сессия, сеть не тревожим.
        await message.answer(remote_bridge.refusal_text(remote_bridge.NO_DEVICE))
        return
    publications = await _load(message)
    if publications is None:
        return
    selection = remote_bridge.select_target(
        publications,
        binding.device_id,
        now=time.time(),
        capability="stop",
        conversation_id=binding.conversation_id,
    )
    if selection.publication is None:
        await message.answer(remote_bridge.refusal_text(selection.refusal or "rc_unavailable"))
        return
    key = remote_bridge.idempotency_key(
        "stop",
        message.chat.id,
        message.message_thread_id or 0,
        message.message_id,
    )
    try:
        await herecrm_client.rc_create_command(
            selection.publication.id,
            command_type="stop",
            idempotency_key=key,
        )
    except HereCrmClientError as error:
        await message.answer(remote_bridge.refusal_text(error.code))
        return
    await message.answer("⏹ Команда остановки отправлена устройству.")


async def _detach(message: Message) -> None:
    conv = _conversation(message, message.from_user.id if message.from_user else 0)
    binding = remote_bridge.conversation_binding(conv)
    if not binding.active:
        await message.answer("Удалённый режим и так выключен для этого треда.")
        return
    try:
        repo.set_remote_device(int(conv["id"]), None, None, None, None)
    except (sqlite3.Error, ValueError, TypeError) as error:
        log.warning("не удалось снять привязку (%s)", type(error).__name__)
        await message.answer("Не удалось снять привязку — попробуй ещё раз.")
        return
    await message.answer(
        f"Привязка к {binding.label} снята: сообщения снова исполняет "
        "серверная сессия бота. Публикация на самом устройстве не закрыта — "
        "закрыть её можно там командой /rc off."
    )


async def _load(message: Message) -> list[remote_bridge.Publication] | None:
    """Тянет публикации владельца; отказ объясняем, а не молчим.

    Просим ``state=all``: закрытую публикацию нужно ВИДЕТЬ, иначе вместо точного
    «публикация закрыта» человек получит расплывчатое «нет публикаций».
    """
    if not herecrm_client.rc_configured():
        await _answer(message, remote_bridge.refusal_text(remote_bridge.NOT_CONFIGURED))
        return None
    try:
        payload = await herecrm_client.rc_publications(state="all")
    except HereCrmClientError as error:
        await _answer(message, remote_bridge.refusal_text(error.code))
        return None
    publications = remote_bridge.parse_publications(payload)
    if any(item.device_name == "устройство" for item in publications):
        # Фолбэк для старых ответов без deviceName. Свежий сервер отдаёт имя сам.
        publications = remote_bridge.parse_publications(payload, await _device_names())
    return publications


async def _device_names() -> dict[str, str]:
    """Фолбэк-имена устройств из ленты диалогов CRM.

    Пустая карта не ошибка: экран выбора останется с нейтральной подписью, но
    сам выбор сессии не сломается.
    """
    if not herecrm_client.configured():
        return {}
    try:
        payload = await herecrm_client.conversations()
    except HereCrmClientError as error:
        log.info("имена устройств недоступны (%s)", error.code)
        return {}
    return remote_bridge.device_names(payload)


def _conversation(message: Message, user_id: int):
    return repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0, user_id)


async def _ack(callback: CallbackQuery, text: str) -> None:
    try:
        await callback.answer(text)
    except TelegramAPIError as error:
        log.warning("не удалось подтвердить нажатие: %s", error)


async def _answer(message: Message, text: str) -> None:
    try:
        await message.answer(text)
    except TelegramAPIError as error:
        log.warning("не удалось ответить в чат: %s", error)
