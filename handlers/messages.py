"""Главный обработчик: с typing-heartbeat, прерыванием, подписью."""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (BufferedInputFile, Message,
                           InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo)

import providers
from core import config, events, changes
from utils import locks, whisper
from utils.files import download_attachment
from utils.markdown import html_escape, markdown_to_html
from utils.table_render import replace_tables_with_placeholders
from . import repo
from .common import is_admin, send_long

router = Router()
log = logging.getLogger("bridge.msg")

PROGRESS_MIN_INTERVAL_SEC = float(os.environ.get("PROGRESS_MIN_INTERVAL_SEC", "1.5"))
PROGRESS_MAX_CHARS = 3500
PROGRESS_ENABLED = os.environ.get("PROGRESS_ENABLED", "1") in ("1", "true", "yes", "on")
PROGRESS_CHAIN_LIMIT = int(os.environ.get("PROGRESS_CHAIN_LIMIT", "15"))
HEARTBEAT_INTERVAL_SEC = float(os.environ.get("HEARTBEAT_INTERVAL_SEC", "1.0"))
TYPING_INTERVAL_SEC = float(os.environ.get("TYPING_INTERVAL_SEC", "4"))
INTERRUPT_ON_NEW = os.environ.get("INTERRUPT_ON_NEW_MESSAGE", "1") in ("1", "true", "yes", "on")

# Финальный текст длиннее этого порога — кладём в .md файл, в чат шлём превью.
# Цифра подобрана под лимит Telegram (4096) с запасом на header/chain/signature.
LONG_TEXT_LIMIT = int(os.environ.get("LONG_TEXT_LIMIT", "3500"))
LONG_STEPS_LIMIT = int(os.environ.get("LONG_STEPS_LIMIT", "15"))
PREVIEW_LIMIT = int(os.environ.get("PREVIEW_LIMIT", "1500"))

# Защита от Telegram FloodWait на edit прогресс-сообщения:
# адаптивный интервал между edit'ами и «тихий» режим для долгих задач.
PROGRESS_MAX_INTERVAL_SEC = float(os.environ.get("PROGRESS_MAX_INTERVAL_SEC", "15.0"))
PROGRESS_BACKOFF_FACTOR = float(os.environ.get("PROGRESS_BACKOFF_FACTOR", "1.6"))
PROGRESS_RESET_SUCCESSES = int(os.environ.get("PROGRESS_RESET_SUCCESSES", "5"))
PROGRESS_QUIET_AFTER_SEC = float(os.environ.get("PROGRESS_QUIET_AFTER_SEC", "600"))
PROGRESS_QUIET_INTERVAL_SEC = float(os.environ.get("PROGRESS_QUIET_INTERVAL_SEC", "30.0"))
PROGRESS_HEARTBEAT_IDLE_SEC = float(os.environ.get("PROGRESS_HEARTBEAT_IDLE_SEC", "30.0"))

# Активные задачи по (chat_id, thread_id) → asyncio.Task
# нужны чтобы при новом сообщении отменить предыдущую
_active_tasks: dict[tuple[int, int], asyncio.Task] = {}

# Буфер для дебаунса: длинное сообщение в Telegram разбивается на части (>4096),
# и каждая часть приходит отдельным апдейтом. Чтобы не запускать обработку трижды,
# собираем поступающие подряд сообщения и обрабатываем их как один склеенный промпт.
_pending: dict[tuple[int, int], dict] = {}
DEBOUNCE_SEC = float(os.environ.get("DEBOUNCE_SEC", "1.5"))

# Счётчик «занят»: ++на старте _process_message, --в конце.
# Используется restart_watcher'ом в bot.py чтобы не рестартить посреди ответа.
_busy_counter: int = 0


def is_busy() -> bool:
    """Идёт ли сейчас обработка какого-либо запроса (для restart-логики)."""
    if _busy_counter > 0:
        return True
    if any(not t.done() for t in _active_tasks.values()):
        return True
    if _pending:
        return True
    return False


def _extract_user_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def _make_preview(md_text: str, limit: int = PREVIEW_LIMIT) -> str:
    """Превью длинного ответа — обрезаем по последнему естественному разделителю."""
    if len(md_text) <= limit:
        return md_text
    cut = md_text[:limit]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[:idx].rstrip() + "\n\n…"
    return cut + "…"


def _format_signature(model: str | None, duration_s: float, edits: list) -> str:
    """Подпись под ответом."""
    parts = []
    if model:
        parts.append(model)
    parts.append(f"{duration_s:.1f}с")
    if edits:
        total_added = sum(e.get("added", 0) for e in edits)
        total_removed = sum(e.get("removed", 0) for e in edits)
        if total_added or total_removed:
            parts.append(f"+{total_added} −{total_removed} строк")
        # уникальные файлы по имени, в порядке появления
        files: list[str] = []
        for e in edits:
            name = os.path.basename((e.get("file") or "?").rstrip("/\\")) or "?"
            if name not in files:
                files.append(name)
        n = len(files)
        word = "файл" if n == 1 else ("файла" if 2 <= n <= 4 else "файлов")
        if n <= 3:
            parts.append(f"{n} {word}: " + ", ".join(files))
        else:
            parts.append(f"{n} {word}: " + ", ".join(files[:3]) + f" +ещё {n - 3}")
    parts.append(f"обновлено {time.strftime('%H:%M:%S')}")
    return "\n\n— " + " · ".join(parts)


@router.message(F.text | F.document | F.photo | F.audio | F.voice | F.video | F.video_note)
async def handle_any(message: Message, bot: Bot):
    if message.text and message.text.startswith("/"):
        return
    if not is_admin(message):
        await message.answer(f"Access denied. id={message.from_user.id if message.from_user else '?'}")
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    user_id = message.from_user.id
    key = (chat_id, thread_id)

    user_text = _extract_user_text(message)
    attachment_path: Path | None = await download_attachment(bot, message)
    if attachment_path:
        log.info("downloaded attachment to %s", attachment_path)
    if not user_text and not attachment_path:
        return

    log.info("handle from user=%s chat=%s thread=%s | text=%r | attachment=%s",
             user_id, chat_id, thread_id, user_text[:200],
             attachment_path.name if attachment_path else None)

    # --- ДЕБАУНС: длинные сообщения Telegram разбивает на части ---
    pending = _pending.get(key)
    if pending is not None:
        if pending.get("timer") and not pending["timer"].done():
            pending["timer"].cancel()
    else:
        pending = {"texts": [], "attachments": [], "last_message": None, "timer": None}
        _pending[key] = pending

    if user_text:
        pending["texts"].append(user_text)
    if attachment_path:
        pending["attachments"].append(attachment_path)
    pending["last_message"] = message

    # таймер flush — отдельная задача со sleep
    async def _delayed_flush():
        try:
            await asyncio.sleep(DEBOUNCE_SEC)
            await _flush_pending(bot, key)
        except asyncio.CancelledError:
            pass

    pending["timer"] = asyncio.create_task(_delayed_flush())


async def _flush_pending(bot: Bot, key: tuple[int, int]):
    pending = _pending.pop(key, None)
    if not pending or not pending.get("last_message"):
        return

    message = pending["last_message"]
    chat_id, thread_id = key
    user_id = message.from_user.id

    conv = repo.get_or_create_conv(chat_id, thread_id, user_id)
    if not conv["account_id"]:
        await message.answer("Не выбран аккаунт. /accounts")
        return

    # склеиваем тексты в один промпт
    texts = pending["texts"]
    attachments = pending["attachments"]

    # --- ГОЛОСОВЫЕ: расшифровываем через Whisper и подмешиваем текстом ---
    voice_transcripts: list[str] = []
    remaining_attachments: list = []
    for a in attachments:
        if whisper.is_voice_file(a):
            try:
                status_msg = await message.answer("🎙 расшифровываю голосовое…")
            except Exception:
                status_msg = None
            try:
                text = await whisper.transcribe(a)
                voice_transcripts.append(text)
                if status_msg:
                    try:
                        await status_msg.edit_text(f"🎙 расшифровано: {text[:200]}"
                                                   + ("…" if len(text) > 200 else ""))
                    except Exception:
                        pass
            except Exception as e:
                log.warning("whisper failed for %s: %s", a, e)
                voice_transcripts.append(f"[не удалось расшифровать {a.name}]")
                if status_msg:
                    try:
                        await status_msg.edit_text(f"❌ Whisper упал: {type(e).__name__}")
                    except Exception:
                        pass
                remaining_attachments.append(a)  # оставим файл для CLI как путь
        else:
            remaining_attachments.append(a)
    attachments = remaining_attachments

    user_text = "\n".join(texts) if texts else ""
    if voice_transcripts:
        joined = "\n".join(voice_transcripts)
        user_text = (user_text + "\n" + joined).strip() if user_text else joined
    if not user_text and attachments:
        user_text = f"(пользователь прислал файлы: {', '.join(a.name for a in attachments)})"
    # для простоты передаём первое вложение (если их несколько — остальные упоминаются в тексте)
    main_attachment = attachments[0] if attachments else None
    if len(attachments) > 1:
        extra = "\n".join(f"- {a}" for a in attachments[1:])
        user_text += f"\n\n[Доп. вложения]\n{extra}"

    if len(texts) > 1:
        log.info("debounce: склеено %d сообщений в один промпт (chat=%s thread=%s)",
                 len(texts), chat_id, thread_id)

    # --- ПРЕРЫВАНИЕ ПРЕДЫДУЩЕЙ ЗАДАЧИ ---
    prev = _active_tasks.get(key)
    if prev and not prev.done():
        if INTERRUPT_ON_NEW:
            log.info("interrupting previous task for chat=%s thread=%s", chat_id, thread_id)
            prev.cancel()
            try:
                await message.answer("⏸ Предыдущий запрос прерван, начинаю новый.")
            except Exception:
                pass
            try:
                await asyncio.wait_for(prev, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        else:
            await message.answer("⏳ Уже выполняю задачу — поставил в очередь.")

    task = asyncio.create_task(
        _process_message(bot, message, conv, user_text, main_attachment,
                          all_attachments=attachments)
    )
    _active_tasks[key] = task


async def _process_message(bot: Bot, message: Message, conv,
                           user_text: str, attachment_path: Optional[Path],
                           all_attachments: Optional[list[Path]] = None):
    global _busy_counter
    _busy_counter += 1
    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    user_id = message.from_user.id
    key = (chat_id, thread_id)

    # лок мы не используем — у нас «отмена предыдущей», а не очередь
    events.log("message_in", user_id=user_id, chat_id=chat_id, thread_id=thread_id,
               payload={"text_preview": user_text[:500], "len": len(user_text),
                        "attachment": str(attachment_path) if attachment_path else None})

    t0 = time.time()

    # Инфо об аккаунте — нужно сразу, чтобы показать в шапке прогресса и в финале
    account = repo.get_account(conv["account_id"])
    account_label = account["label"] if account else None
    account_notes = account["notes"] if account else None

    # --- TYPING HEARTBEAT ---
    typing_stop = asyncio.Event()

    async def typing_heartbeat():
        try:
            while not typing_stop.is_set():
                try:
                    await bot.send_chat_action(chat_id, "typing")
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(typing_stop.wait(), timeout=TYPING_INTERVAL_SEC)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    typing_task = asyncio.create_task(typing_heartbeat())

    # --- live-message state ---
    ps = {
        "msg": None,                  # type: Message | None
        "last_partial": "",
        "last_meta": {},
        "last_displayed": "",
        "last_edit_ts": 0.0,
        "last_event_ts": time.time(),  # время последнего реального события (tool_use / delta)
        "overflowed": False,
        "cooldown_until": 0.0,        # Telegram FloodWait — не редактируем до этого момента
        # адаптивный интервал между edit-операциями
        "min_interval": PROGRESS_MIN_INTERVAL_SEC,
        "success_streak": 0,
        "quiet_mode": False,          # включён после PROGRESS_QUIET_AFTER_SEC — редкие обновления
        "attachments": list(all_attachments or ([attachment_path] if attachment_path else [])),
    }

    def _render() -> str:
        elapsed = int(time.time() - t0)
        meta = ps["last_meta"] or {}
        current_tool = meta.get("current_tool")
        chain = meta.get("tool_call_log") or []

        head = []
        model_name = conv["model"]
        if model_name:
            head.append(f"🤖 {html_escape(model_name)}")
        if account_label:
            head.append(f"👤 {html_escape(account_label)}")
        if account_notes:
            head.append(f"📝 {html_escape(account_notes)}")
        # После 10 минут переходим на минуты — иначе render меняется КАЖДУЮ секунду
        # и каждый heartbeat считает дисплей «обновлённым» (а edit→Telegram = FloodWait).
        if elapsed >= 60:
            head.append(f"⌛ {elapsed // 60} мин")
        else:
            head.append(f"⌛ {elapsed}с")
        if current_tool:
            head.append(f"🔧 {html_escape(current_tool)}")
        parts = [" · ".join(head)]

        if ps["quiet_mode"]:
            parts.append(
                "🔇 <i>Работаю молча — задача длинная, прогресс приглушён, "
                "чтобы не упереться в лимит Telegram. Финальный ответ придёт отдельно.</i>"
            )

        # уведомление о полученных вложениях — сразу видно что бот их принял
        attachments = ps.get("attachments") or []
        if attachments:
            att_lines = []
            for p in attachments[:5]:
                name = p.name if hasattr(p, "name") else str(p)
                low = name.lower()
                if low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
                    icon = "📷"
                elif low.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi")):
                    icon = "🎬"
                elif low.endswith((".mp3", ".ogg", ".oga", ".m4a", ".wav", ".flac")):
                    icon = "🎵"
                elif low.endswith((".pdf",)):
                    icon = "📄"
                else:
                    icon = "📎"
                att_lines.append(f"{icon} {html_escape(name)}")
            if len(attachments) > 5:
                att_lines.append(f"… ещё {len(attachments) - 5}")
            parts.append("Получено:\n" + "\n".join(att_lines))

        # --- цепочка шагов (валидный HTML, всегда влезает) ---
        chain_part = ""
        if chain:
            total = len(chain)
            start = max(0, total - PROGRESS_CHAIN_LIMIT)
            shown = chain[start:]
            lines = []
            if start > 0:
                lines.append(f"… (показано {len(shown)} из {total})")
            for i, item in enumerate(shown, start + 1):
                s = str(item)
                if len(s) > 200:                  # длинные команды не раздувают сообщение
                    s = s[:200] + "…"
                lines.append(f"{i}. {html_escape(s)}")
            chain_body = "\n".join(lines)
            chain_part = (f"📋 Шаги ({total})\n"
                          f"<blockquote expandable>{chain_body}</blockquote>")

        # --- частичный текст: режем СЫРОЙ текст ДО конвертации в HTML.
        # Слайс готового HTML рубит теги/сущности → Telegram "can't parse entities". ---
        raw_partial = ps["last_partial"] or ""
        partial_html = ""
        if raw_partial:
            if len(raw_partial) > PROGRESS_MAX_CHARS:
                ps["overflowed"] = True
                partial_html = (markdown_to_html(raw_partial[-PROGRESS_MAX_CHARS:])
                                + "\n…⏳ продолжаю, финал придёт отдельно")
            else:
                partial_html = markdown_to_html(raw_partial)

        # --- сборка с бюджетом. fixed (голова+шаги) — валидный HTML под лимитом.
        # Текст добавляем только если влезает; не влезает — режем СЫРОЙ хвост или опускаем. ---
        LIMIT, SAFE = 4096, 3800
        fixed = "\n\n".join(parts)
        if chain_part:
            fixed += "\n\n" + chain_part

        if not chain and not raw_partial:
            return fixed + "\n\n💭 думаю…"

        if partial_html:
            budget = SAFE - len(fixed) - 2
            if budget < 150:
                return fixed                       # шаги почти заполнили лимит — текст опускаем
            if len(partial_html) > budget:
                partial_html = markdown_to_html(raw_partial[-budget:])
                if len(fixed) + 2 + len(partial_html) > LIMIT:
                    return fixed                   # страховка — лучше без текста, чем битый HTML
            fixed += "\n\n" + partial_html
        return fixed

    def _bump_backoff(wait_sec: float):
        """FloodWait — увеличиваем min_interval, сбрасываем серию успехов."""
        ps["cooldown_until"] = time.time() + wait_sec + 1.0
        ps["min_interval"] = min(ps["min_interval"] * PROGRESS_BACKOFF_FACTOR,
                                  PROGRESS_MAX_INTERVAL_SEC)
        ps["success_streak"] = 0
        log.warning("progress: FloodWait %.0fs → min_interval=%.1fs",
                    wait_sec, ps["min_interval"])

    async def _push(force: bool = False):
        if not PROGRESS_ENABLED:
            return
        now = time.time()
        # Telegram FloodWait — пока cooldown активен, вообще ничего не шлём
        if now < ps["cooldown_until"]:
            return

        # Включаем «тихий» режим для долгих задач: edit'ы реже, текст-маркер.
        elapsed = now - t0
        if not ps["quiet_mode"] and elapsed > PROGRESS_QUIET_AFTER_SEC:
            ps["quiet_mode"] = True
            log.info("progress: quiet mode after %.0fs", elapsed)

        # Минимальный интервал — адаптивный, в quiet ещё длиннее.
        min_interval = ps["min_interval"]
        if ps["quiet_mode"]:
            min_interval = max(min_interval, PROGRESS_QUIET_INTERVAL_SEC)
        if ps["msg"] is not None and now - ps["last_edit_ts"] < min_interval:
            return

        display = _render()
        if display == ps["last_displayed"]:
            return
        try:
            if ps["msg"] is None:
                ps["msg"] = await message.answer(display, parse_mode="HTML")
            else:
                await ps["msg"].edit_text(display, parse_mode="HTML")
            ps["last_displayed"] = display
            ps["last_edit_ts"] = now
            # успех — пошагово сбрасываем интервал к базовому
            ps["success_streak"] += 1
            if (ps["success_streak"] >= PROGRESS_RESET_SUCCESSES
                    and ps["min_interval"] > PROGRESS_MIN_INTERVAL_SEC):
                ps["min_interval"] = max(PROGRESS_MIN_INTERVAL_SEC,
                                          ps["min_interval"] / PROGRESS_BACKOFF_FACTOR)
                ps["success_streak"] = 0
        except TelegramRetryAfter as e:
            _bump_backoff(float(getattr(e, "retry_after", 30) or 30))
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "not modified" in msg:
                return
            if "too many requests" in msg or "retry after" in msg:
                import re
                m = re.search(r"retry.*?(\d+)", msg)
                _bump_backoff(float(m.group(1)) if m else 60.0)
            else:
                log.warning("progress edit failed: %s", e)
        except Exception as e:
            msg = str(e).lower()
            if "too many requests" in msg or "flood control" in msg or "retry after" in msg:
                import re
                m = re.search(r"retry.*?(\d+)", msg)
                _bump_backoff(float(m.group(1)) if m else 60.0)
            else:
                log.warning("progress edit unexpected error: %s", e)

    async def progress_cb(partial_text: str, event_type: str, meta: dict):
        if ps["overflowed"]:
            return
        if partial_text is not None:
            ps["last_partial"] = partial_text
        if meta:
            ps["last_meta"] = meta
        ps["last_event_ts"] = time.time()
        force = event_type in ("tool_use", "tool_start")
        await _push(force=force)

    # --- HEARTBEAT: периодически перерисовываем секундомер ---
    heartbeat_stop = asyncio.Event()

    async def heartbeat():
        try:
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(),
                                            timeout=HEARTBEAT_INTERVAL_SEC)
                    break
                except asyncio.TimeoutError:
                    pass
                if ps["msg"] is None or ps["overflowed"]:
                    continue
                # Если давно не было реальных событий — не дёргаем edit,
                # секундомер пусть подождёт. Сильно экономит лимит Telegram.
                idle = time.time() - ps["last_event_ts"]
                if idle > PROGRESS_HEARTBEAT_IDLE_SEC:
                    continue
                await _push(force=False)
        except asyncio.CancelledError:
            pass

    heartbeat_task = asyncio.create_task(heartbeat())

    # моментальный отклик: создаём прогресс-сообщение ДО запуска CLI,
    # иначе пользователь несколько секунд видит только typing-индикатор
    await _push(force=True)

    # --- основная работа ---
    try:
        prov = providers.make(account)

        if conv["provider_session_id"]:
            prompt = user_text
        else:
            prompt = repo.build_prompt_with_history(conv, user_text)

        repo.save_message(conv["id"], "user", user_text,
                          provider=account["provider"], model=conv["model"])

        text, new_session, meta = await prov.run(
            prompt=prompt,
            cwd=conv["cwd"] or config.DEFAULT_CWD,
            session_id=conv["provider_session_id"],
            model=conv["model"],
            attachments=[attachment_path] if attachment_path else None,
            progress=progress_cb,
        )

        duration_s = time.time() - t0

        repo.save_message(conv["id"], "assistant", text,
                          provider=account["provider"], model=conv["model"])

        # Markdown-таблицы в Telegram нечитаемы — вырезаем их и шлём картинками.
        table_pngs: list[bytes] = []
        try:
            text, table_pngs = replace_tables_with_placeholders(text)
        except Exception as e:
            log.warning("table extraction failed: %s", e)
        if new_session and new_session != conv["provider_session_id"]:
            repo.update_conv(conv["id"], provider_session_id=new_session)

        # Журнал изменений: полный дифф каждой правки (отдельный слой, core.changes).
        # Временные файлы из .runtime (диагностические скрипты бота) — не показываем.
        _full_edits = [e for e in (meta.get("edits") or [])
                       if ".runtime" not in (e.get("file") or "").replace("\\", "/")]
        try:
            changes.record_edits(thread_id=thread_id, account=account["label"],
                                  model=conv["model"], edits=_full_edits)
        except Exception as _e:
            log.warning("changes journal failed: %s", _e)

        events.log("message_out", user_id=user_id, chat_id=chat_id, thread_id=thread_id,
                   account_label=account["label"], provider=account["provider"],
                   model=conv["model"],
                   tokens_in=meta.get("tokens_in"), tokens_out=meta.get("tokens_out"),
                   duration_ms=int(duration_s * 1000),
                   payload={"out_len": len(text),
                            "edits": changes.trim_edits_for_events(_full_edits),
                            "tool_uses": meta.get("tool_uses")})

        # ---------- финальная отправка ----------
        signature = _format_signature(conv["model"], duration_s, meta.get("edits") or [])

        # Шапка финального сообщения: модель / название аккаунта / заметка
        header_parts = []
        if conv["model"]:
            header_parts.append(f"🤖 {html_escape(conv['model'])}")
        if account_label:
            header_parts.append(f"👤 {html_escape(account_label)}")
        if account_notes:
            header_parts.append(f"📝 {html_escape(account_notes)}")
        header_block = (" · ".join(header_parts) + "\n\n") if header_parts else ""

        # Файлы, которые досылаем после финального текста (большой ответ / много шагов).
        # Каждый элемент: (bytes, filename).
        attachments_to_send: list[tuple[bytes, str]] = []
        ts_tag = time.strftime("%H%M%S")

        # ---- основной текст ----
        if len(text) > LONG_TEXT_LIMIT:
            # длинный → в файл, в чат идёт превью
            text_for_display = _make_preview(text) + "\n\n📄 _Полный ответ — в прикреплённом файле_"
            md_bytes = ("﻿" + text).encode("utf-8")  # UTF-8 BOM для Windows-просмотрщиков
            attachments_to_send.append((md_bytes, f"answer-{ts_tag}.md"))
        else:
            text_for_display = text

        # ---- блок шагов ----
        # Шаги вставляем ИНЛАЙН только если весь итог влезает в лимит Telegram (~4096).
        # Иначе — отдельным файлом, чтобы основной ответ НЕ резался посередине.
        body_html = markdown_to_html(text_for_display)
        sig_html = html_escape(signature)
        chain = (ps["last_meta"] or {}).get("tool_call_log") or []
        chain_block = ""
        if chain:
            base_len = len(header_block) + len(body_html) + len(sig_html)
            inline_lines = [html_escape(f"{i}. {item}") for i, item in enumerate(chain, 1)]
            inline = (f"\n\n📋 Шаги ({len(chain)})\n"
                      f"<blockquote expandable>{chr(10).join(inline_lines)}</blockquote>")
            if len(chain) <= LONG_STEPS_LIMIT and base_len + len(inline) <= 3900:
                chain_block = inline
            else:
                # не влезает — шаги в файл, в сообщении только короткая пометка
                chain_block = f"\n\n📋 Шаги ({len(chain)}) — <i>в прикреплённом файле</i>"
                steps_lines = [f"{i}. {item}" for i, item in enumerate(chain, 1)]
                steps_bytes = ("﻿" + "\n".join(steps_lines)).encode("utf-8")
                attachments_to_send.append((steps_bytes, f"steps-{ts_tag}.txt"))

        final_text = header_block + body_html + chain_block + sig_html

        # Кнопка-вебап на правки ИМЕННО этого запроса (окно времени treda).
        # web_app → откроется как Mini App в Telegram (с авторизацией).
        edits_markup = None
        if _full_edits and config.WEBAPP_URL:
            web_url = (f"{config.WEBAPP_URL}/edits?thread={thread_id}"
                       f"&since={int(t0)}&until={int(time.time()) + 5}")
            try:
                edits_markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text=f"📋 Правки этого запроса ({len(_full_edits)})",
                        web_app=WebAppInfo(url=web_url),
                    )
                ]])
            except Exception:
                edits_markup = None
        _btn = {"done": False}

        # остановим heartbeat перед финальной заменой
        heartbeat_stop.set()
        try:
            await asyncio.wait_for(heartbeat_task, timeout=2)
        except Exception:
            heartbeat_task.cancel()

        async def _final_edit():
            await ps["msg"].edit_text(final_text or "(пусто)", parse_mode="HTML",
                                      reply_markup=edits_markup)
            _btn["done"] = edits_markup is not None

        async def _final_send():
            await send_long(message, final_text, html_already=True)

        async def _with_retry(coro_factory):
            """Один повтор при TelegramRetryAfter — ждём указанное время и пробуем снова."""
            try:
                await coro_factory()
                return True
            except TelegramRetryAfter as e:
                wait = float(getattr(e, "retry_after", 10) or 10) + 1.0
                log.warning("final: FloodWait, sleep %.0fs and retry", wait)
                await asyncio.sleep(wait)
                try:
                    await coro_factory()
                    return True
                except Exception as e2:
                    log.warning("final retry failed: %s", e2)
                    return False
            except TelegramBadRequest as e:
                if "not modified" in str(e).lower():
                    return True
                return False
            except Exception as e:
                log.warning("final send failed: %s", e)
                return False

        if ps["msg"] is not None and not ps["overflowed"] and len(final_text) <= 4000:
            ok = await _with_retry(_final_edit)
            if not ok:
                await _with_retry(_final_send)
        else:
            if ps["msg"] is not None:
                try:
                    tail = (ps["last_displayed"].split("\n\n", 1)[-1])[:4000] or "(прогресс)"
                    await ps["msg"].edit_text(tail, parse_mode="HTML")
                except Exception:
                    pass
            await _with_retry(_final_send)

        # если кнопку не удалось повесить на финальное сообщение (ушли через send_long) —
        # шлём её отдельным коротким сообщением
        if edits_markup is not None and not _btn["done"]:
            try:
                await message.answer("📋 Правки этого запроса — открыть в вебапе:",
                                     reply_markup=edits_markup)
            except Exception as e:
                log.warning("edits button send failed: %s", e)

        # после финального текста — досылаем файлы (длинный ответ, длинные шаги)
        for data, fname in attachments_to_send:
            try:
                doc = BufferedInputFile(data, filename=fname)
                await bot.send_document(
                    chat_id=chat_id,
                    message_thread_id=thread_id if thread_id else None,
                    document=doc,
                )
            except Exception as e:
                log.warning("send attachment %s failed: %s", fname, e)

        # затем — картинки с таблицами
        for idx, png in enumerate(table_pngs, 1):
            try:
                photo = BufferedInputFile(png, filename=f"table_{idx}.png")
                await bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=thread_id if thread_id else None,
                    photo=photo,
                )
            except Exception as e:
                log.warning("send table png #%d failed: %s", idx, e)

    except asyncio.CancelledError:
        log.info("task cancelled chat=%s thread=%s", chat_id, thread_id)
        if ps["msg"] is not None:
            try:
                await ps["msg"].edit_text(
                    (ps["last_displayed"] or "(прервано)") + "\n\n⏸ Прервано",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        raise

    except Exception as e:
        log.exception("handler error")
        events.log("error", user_id=user_id, chat_id=chat_id, thread_id=thread_id,
                   duration_ms=int((time.time() - t0) * 1000),
                   payload={"type": type(e).__name__, "message": str(e)[:500]})
        err_text = html_escape(f"Ошибка: {type(e).__name__}: {str(e)[:1500]}")
        if ps["msg"] is not None:
            try:
                await ps["msg"].edit_text(err_text, parse_mode="HTML")
            except Exception:
                await message.answer(err_text, parse_mode="HTML")
        else:
            await message.answer(err_text, parse_mode="HTML")

    finally:
        heartbeat_stop.set()
        if not heartbeat_task.done():
            heartbeat_task.cancel()
        typing_stop.set()
        try:
            await asyncio.wait_for(typing_task, timeout=1)
        except Exception:
            typing_task.cancel()
        if _active_tasks.get(key) is asyncio.current_task():
            _active_tasks.pop(key, None)
        _busy_counter = max(0, _busy_counter - 1)
