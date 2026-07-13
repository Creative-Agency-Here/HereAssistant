"""Главный обработчик: с typing-heartbeat, прерыванием, подписью."""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import providers
from core import changes, config, crm_sync, events, project_config
from utils import rich
from utils.files import download_attachment
from utils.markdown import html_escape

from . import repo
from .common import is_allowed
from .message_attachments import prepare_message_input
from .message_buffer import enqueue_message
from .message_final import prepare_final_payload
from .message_final_delivery import FinalDelivery, FinalDeliveryRequest
from .message_formatting import format_signature, should_skip_edit
from .message_live import LiveSessionPolicy, MessageLiveSession
from .message_rich_final import deliver_rich_final, prepare_classic_tables
from .message_state import runtime

router = Router()
log = logging.getLogger("bridge.msg")

PROGRESS_MIN_INTERVAL_SEC = float(os.environ.get("PROGRESS_MIN_INTERVAL_SEC", "1.5"))
# Троттлинг rich-драфтов (sendRichMessageDraft): анимируемое превью ответа.
DRAFT_MIN_INTERVAL_SEC = float(os.environ.get("DRAFT_MIN_INTERVAL_SEC", "1.0"))
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

DEBOUNCE_SEC = float(os.environ.get("DEBOUNCE_SEC", "1.5"))


def is_busy() -> bool:
    """Идёт ли сейчас обработка какого-либо запроса (для restart-логики)."""
    return runtime.is_busy()


def _extract_user_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


@router.message(F.text | F.document | F.photo | F.audio | F.voice | F.video | F.video_note)
async def handle_any(message: Message, bot: Bot):
    if message.text and message.text.startswith("/"):
        return
    if not is_allowed(message):
        # незнакомец/ожидающий: ответ по режиму доступа (заявка владельцу и т.п.)
        from .team import handle_unauthorized

        await handle_unauthorized(message, bot)
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    user_id = message.from_user.id
    key = (user_id, chat_id, thread_id)

    user_text = _extract_user_text(message)
    attachment_path: Path | None = await download_attachment(bot, message, user_id)
    if attachment_path:
        log.info("downloaded attachment to %s", attachment_path)
    if not user_text and not attachment_path:
        return

    log.info(
        "handle from user=%s chat=%s thread=%s | text=%r | attachment=%s",
        user_id,
        chat_id,
        thread_id,
        user_text[:200],
        attachment_path.name if attachment_path else None,
    )

    enqueue_message(
        runtime,
        key=key,
        text=user_text,
        attachment=attachment_path,
        message=message,
        delay=DEBOUNCE_SEC,
        flush=lambda pending_key: _flush_pending(bot, pending_key),
    )


async def _flush_pending(bot: Bot, key: tuple[int, int, int]):
    pending = runtime.pending.pop(key, None)
    if not pending or not pending.get("last_message"):
        return

    message = pending["last_message"]
    user_id, chat_id, thread_id = key

    conv = repo.get_or_create_conv(chat_id, thread_id, user_id)
    if not conv["account_id"]:
        await message.answer(
            f"{repo.ACCOUNT_NOT_AVAILABLE}: для пользователя нет своего или явно общего аккаунта."
        )
        return

    # склеиваем тексты в один промпт
    texts = pending["texts"]
    attachments = pending["attachments"]

    prepared = await prepare_message_input(
        message,
        texts,
        attachments,
        logger=log,
    )
    user_text = prepared.text
    main_attachment = prepared.main_attachment
    attachments = prepared.attachments

    if len(texts) > 1:
        log.info(
            "debounce: склеено %d сообщений в один промпт (chat=%s thread=%s)",
            len(texts),
            chat_id,
            thread_id,
        )

    # --- ПРЕРЫВАНИЕ ПРЕДЫДУЩЕЙ ЗАДАЧИ ---
    prev = runtime.active_tasks.get(key)
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
        _process_message(
            bot, message, conv, user_text, main_attachment, all_attachments=attachments
        )
    )
    runtime.active_tasks[key] = task


async def _process_message(
    bot: Bot,
    message: Message,
    conv,
    user_text: str,
    attachment_path: Optional[Path],
    all_attachments: Optional[list[Path]] = None,
):
    runtime.mark_started()
    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    user_id = message.from_user.id
    key = (user_id, chat_id, thread_id)

    # Privacy-политика проекта (.hereassistant/project.yml по cwd; default private).
    # Решает, что можно сохранять в БД/журналы. Метрики (длины/токены/время) — можно всегда.
    policy = project_config.policy_for(conv["cwd"] or config.DEFAULT_CWD)

    # лок мы не используем — у нас «отмена предыдущей», а не очередь
    if project_config.can_store_messages(policy):
        events.log(
            "message_in",
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            payload={
                "text_preview": user_text[:500],
                "len": len(user_text),
                "attachment": str(attachment_path) if attachment_path else None,
            },
        )
    else:
        # private/local без разрешения: только метаданные, без текста и имён файлов
        events.log(
            "message_in",
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            payload={"len": len(user_text), "private": True, "attachment": bool(attachment_path)},
        )

    t0 = time.time()

    # Инфо об аккаунте — нужно сразу, чтобы показать в шапке прогресса и в финале
    account = repo.get_account(conv["account_id"], user_id)
    if account is None:
        await message.answer(
            f"{repo.ACCOUNT_NOT_AVAILABLE}: выбранный аккаунт недоступен пользователю."
        )
        runtime.mark_finished()
        return
    account_label = account["label"] if account else None
    account_notes = account["notes"] if account else None

    live = MessageLiveSession(
        bot=bot,
        source_message=message,
        model=conv["model"],
        account_label=account_label,
        account_notes=account_notes,
        attachments=list(all_attachments or ([attachment_path] if attachment_path else [])),
        started_at=t0,
        rich_stream_enabled=rich.stream_enabled() and message.chat.type == "private",
        policy=LiveSessionPolicy(
            progress_enabled=PROGRESS_ENABLED,
            progress_min_interval=PROGRESS_MIN_INTERVAL_SEC,
            progress_max_interval=PROGRESS_MAX_INTERVAL_SEC,
            progress_backoff_factor=PROGRESS_BACKOFF_FACTOR,
            progress_reset_successes=PROGRESS_RESET_SUCCESSES,
            progress_quiet_after=PROGRESS_QUIET_AFTER_SEC,
            progress_quiet_interval=PROGRESS_QUIET_INTERVAL_SEC,
            progress_chain_limit=PROGRESS_CHAIN_LIMIT,
            progress_max_chars=PROGRESS_MAX_CHARS,
            progress_heartbeat_interval=HEARTBEAT_INTERVAL_SEC,
            progress_heartbeat_idle=PROGRESS_HEARTBEAT_IDLE_SEC,
            typing_interval=TYPING_INTERVAL_SEC,
            draft_min_interval=DRAFT_MIN_INTERVAL_SEC,
        ),
        logger=log,
    )
    progress_state = live.state
    await live.start()

    # --- основная работа ---
    prov = None
    try:
        prov = providers.make(account, user_id=user_id)

        if conv["provider_session_id"]:
            prompt = user_text
        else:
            prompt = repo.build_prompt_with_history(conv, user_text)

        # Содержимое сообщений пишем только с разрешения политики проекта.
        if project_config.can_store_messages(policy):
            repo.save_message(
                conv["id"], "user", user_text, provider=account["provider"], model=conv["model"]
            )

        text, new_session, meta = await prov.run(
            prompt=prompt,
            cwd=conv["cwd"] or config.DEFAULT_CWD,
            session_id=conv["provider_session_id"],
            model=conv["model"],
            attachments=[attachment_path] if attachment_path else None,
            progress=live.progress_callback,
        )

        duration_s = time.time() - t0

        if project_config.can_store_messages(policy):
            repo.save_message(
                conv["id"], "assistant", text, provider=account["provider"], model=conv["model"]
            )

        # Оригинал ответа (с таблицами) — для rich-финала (Bot API 10.1).
        raw_answer = text
        # Markdown-таблицы в Telegram нечитаемы — вырезаем в PNG, но только для
        # классического пути: rich рендерит таблицы нативно. Если rich-финал не
        # пройдёт — извлечём таблицы позже, в фолбэке.
        rich_enabled = rich.enabled()
        classic_prepared = prepare_classic_tables(
            text,
            rich_enabled=rich_enabled,
            logger=log,
        )
        text = classic_prepared.answer
        table_pngs = list(classic_prepared.table_pngs)
        if new_session and new_session != conv["provider_session_id"]:
            repo.update_conv(conv["id"], provider_session_id=new_session)

        # Журнал изменений: полный дифф каждой правки (отдельный слой, core.changes).
        # НЕ журналим временные/секретные файлы (диагностика, askpass с паролями, /tmp и т.п.).
        _full_edits = [
            edit for edit in (meta.get("edits") or []) if not should_skip_edit(edit.get("file"))
        ]
        # Полные диффы — только если политика проекта разрешает журнал правок.
        if project_config.can_store_file_changes(policy):
            try:
                changes.record_edits(
                    user_id=user_id,
                    project_id=conv["project_id"],
                    thread_id=thread_id,
                    account=account["label"],
                    model=conv["model"],
                    edits=_full_edits,
                )
            except Exception as _e:
                log.warning("changes journal failed: %s", _e)

        _out_payload = {"out_len": len(text)}
        if project_config.can_store_messages(policy):
            _out_payload["edits"] = changes.trim_edits_for_events(_full_edits)
            _out_payload["tool_uses"] = meta.get("tool_uses")
        else:
            # приватный проект: без содержимого правок и лога инструментов
            _out_payload["private"] = True
            _out_payload["edits_count"] = len(_full_edits)
        events.log(
            "message_out",
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            account_label=account["label"],
            provider=account["provider"],
            model=conv["model"],
            tokens_in=meta.get("tokens_in"),
            tokens_out=meta.get("tokens_out"),
            duration_ms=int(duration_s * 1000),
            payload=_out_payload,
        )

        # Надёжный M2M sync не участвует в Telegram delivery. Outbox получает
        # только явно разрешённые project.yml типы данных; private/local → no-op.
        crm_sync.enqueue(
            policy,
            crm_sync.Exchange(
                conversation_id=int(conv["id"]),
                telegram_user_id=user_id,
                cwd=conv["cwd"] or config.DEFAULT_CWD,
                project_name=conv["project_name"],
                provider=account["provider"],
                model=conv["model"],
                prompt=user_text,
                answer=raw_answer,
                started_at=t0,
                finished_at=time.time(),
                tokens_in=meta.get("tokens_in"),
                tokens_out=meta.get("tokens_out"),
                duration_ms=int(duration_s * 1000),
            ),
        )

        # ---------- финальная отправка ----------
        signature = format_signature(conv["model"], duration_s, meta.get("edits") or [])

        # Шапка финального сообщения: модель / название аккаунта / заметка
        header_parts = []
        if conv["model"]:
            header_parts.append(f"🤖 {html_escape(conv['model'])}")
        if account_label:
            header_parts.append(f"👤 {html_escape(account_label)}")
        if account_notes:
            header_parts.append(f"📝 {html_escape(account_notes)}")
        header_block = (" · ".join(header_parts) + "\n\n") if header_parts else ""

        rich_result = await deliver_rich_final(
            bot,
            chat_id=chat_id,
            thread_id=thread_id,
            answer=raw_answer,
            model=conv["model"],
            account_label=account_label,
            signature=signature,
            chain=(progress_state.last_meta or {}).get("tool_call_log") or [],
            steps_limit=LONG_STEPS_LIMIT,
            progress_message=progress_state.message,
            rich_enabled=rich_enabled,
            logger=log,
        )
        rich_done = rich_result.done
        if rich_enabled:
            text = rich_result.answer
            table_pngs = list(rich_result.table_pngs)

        chain = [] if rich_done else ((progress_state.last_meta or {}).get("tool_call_log") or [])
        final_payload = prepare_final_payload(
            text,
            header_html=header_block,
            signature=signature,
            chain=chain,
            rich_done=rich_done,
            long_text_limit=LONG_TEXT_LIMIT,
            long_steps_limit=LONG_STEPS_LIMIT,
            preview_limit=PREVIEW_LIMIT,
            timestamp=time.strftime("%H%M%S"),
        )

        # Кнопка-вебап на правки ИМЕННО этого запроса (окно времени treda).
        # web_app → откроется как Mini App в Telegram (с авторизацией).
        edits_markup = None
        if _full_edits and config.WEBAPP_URL:
            web_url = (
                f"{config.webapp_url('/edits')}?thread={thread_id}"
                f"&since={int(t0)}&until={int(time.time()) + 5}"
            )
            if config.WEBAPP_ACCESS_KEY:
                web_url += f"&key={config.WEBAPP_ACCESS_KEY}"
            try:
                edits_markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=f"📋 Правки этого запроса ({len(_full_edits)})",
                                web_app=WebAppInfo(url=web_url),
                            )
                        ]
                    ]
                )
            except Exception:
                edits_markup = None
        # остановим heartbeat перед финальной заменой
        await live.stop_progress()

        await FinalDelivery(
            bot=bot,
            source_message=message,
            progress=progress_state,
            logger=log,
        ).deliver(
            FinalDeliveryRequest(
                html=final_payload.html,
                rich_done=rich_done,
                edits_markup=edits_markup,
                attachments=final_payload.attachments,
                table_pngs=table_pngs,
                chat_id=chat_id,
                thread_id=thread_id,
            )
        )

    except asyncio.CancelledError:
        log.info("task cancelled chat=%s thread=%s", chat_id, thread_id)
        if progress_state.message is not None:
            try:
                await progress_state.message.edit_text(
                    (progress_state.last_displayed or "(прервано)") + "\n\n⏸ Прервано",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        raise

    except Exception as e:
        log.exception("handler error")
        # Текст ошибки может содержать фрагменты приватного вывода CLI — гейтим.
        _err_payload = {"type": type(e).__name__}
        if project_config.can_store_messages(policy):
            _err_payload["message"] = str(e)[:500]
        events.log(
            "error",
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            duration_ms=int((time.time() - t0) * 1000),
            payload=_err_payload,
        )
        err_text = html_escape(f"Ошибка: {type(e).__name__}: {str(e)[:1500]}")
        if progress_state.message is not None:
            try:
                await progress_state.message.edit_text(err_text, parse_mode="HTML")
            except Exception:
                await message.answer(err_text, parse_mode="HTML")
        else:
            await message.answer(err_text, parse_mode="HTML")

    finally:
        if prov is not None:
            prov.cleanup_runtime()
        await live.close()
        if runtime.active_tasks.get(key) is asyncio.current_task():
            runtime.active_tasks.pop(key, None)
        runtime.mark_finished()
