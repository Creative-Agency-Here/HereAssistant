"""Rich Messages (Bot API 10.1): sendRichMessage / sendRichMessageDraft.

Telegram сам парсит markdown в rich-блоки (заголовки, таблицы, код, списки,
математика) — мы отправляем InputRichMessage{"markdown": ...} как есть.
aiogram эти методы ещё не завёз, поэтому зовём Bot API напрямую через aiohttp.

Поведение при ошибках: не бросаем наружу — возвращаем None/False, вызывающий
код откатывается на классический HTML-путь. Если сервер ответил «метод не
найден» (кастомный/старый Bot API endpoint) — выключаемся до конца процесса.
"""

from __future__ import annotations

import json
import logging
import os

import aiohttp

log = logging.getLogger("bridge.rich")

# Флаги: rich-финалы и драфт-стриминг можно выключить по отдельности.
RICH_MESSAGES = os.environ.get("RICH_MESSAGES", "1") in ("1", "true", "yes", "on")
RICH_STREAM = os.environ.get("RICH_STREAM", "1") in ("1", "true", "yes", "on")
# Выше этого порога ответ считаем слишком большим даже для rich → старый путь (.md файл).
RICH_TEXT_LIMIT = int(os.environ.get("RICH_TEXT_LIMIT", "30000"))

# Глобальный рубильник: гаснет при «метод не найден», чтобы не долбить API зря.
_available = True


def enabled() -> bool:
    return RICH_MESSAGES and _available


def stream_enabled() -> bool:
    return RICH_MESSAGES and RICH_STREAM and _available


async def _call(bot, method: str, payload: dict):
    """Сырой вызов Bot API. Возвращает result или None (ошибка уже залогирована)."""
    global _available
    url = f"https://api.telegram.org/bot{bot.token}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        log.warning("%s transport error: %s", method, e)
        return None
    if data.get("ok"):
        return data.get("result")
    desc = str(data.get("description", ""))
    if "method not found" in desc.lower() or "not found" in desc.lower() and "method" in desc.lower():
        _available = False
        log.warning("Bot API не знает %s — rich messages выключены до рестарта", method)
    else:
        # Типовой случай — markdown не распарсился; фолбэк разрулит.
        log.warning("%s failed: %s", method, desc[:300])
    return None


async def send_message(bot, chat_id: int, markdown: str,
                       thread_id: int | None = None):
    """Финальное rich-сообщение. Возвращает Message-dict или None."""
    payload = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown},
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    return await _call(bot, "sendRichMessage", payload)


async def send_draft(bot, chat_id: int, draft_id: int, markdown: str,
                     thread_id: int | None = None) -> bool:
    """Стриминговый драфт (эфемерное превью ~30с, анимируется по draft_id).

    Только для приватных чатов (ограничение Bot API). Финал обязан уйти
    отдельным sendRichMessage — драфт сам по себе в истории не остаётся.
    """
    payload = {
        "chat_id": chat_id,
        "draft_id": draft_id,
        "rich_message": {"markdown": markdown},
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    res = await _call(bot, "sendRichMessageDraft", payload)
    return bool(res)


def sanity_check_markdown(markdown: str) -> bool:
    """Быстрая страховка перед отправкой: не пусто и укладывается в лимит."""
    return bool(markdown.strip()) and len(markdown) <= RICH_TEXT_LIMIT


def debug_dump(markdown: str) -> str:
    """Для логов: компактное описание отправляемого (без содержимого)."""
    return json.dumps({"len": len(markdown), "lines": markdown.count("\n") + 1})
