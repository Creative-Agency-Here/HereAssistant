"""Команда /diff — показать правки последнего ответа в текущем чате."""

import difflib
import json
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core import db
from utils.markdown import html_escape, split_for_telegram

from .common import is_allowed

router = Router()
log = logging.getLogger("bridge.diff")

MAX_DIFF_LINES_PER_FILE = 80  # больше — обрезаем
MAX_MESSAGE_CHARS = 3800


def _format_diff(edit: dict) -> str:
    tool = edit.get("tool", "?")
    file = edit.get("file", "?")
    added = edit.get("added", 0)
    removed = edit.get("removed", 0)
    old = edit.get("old", "") or ""
    new = edit.get("new", "") or ""

    header = f"📝 <b>{html_escape(file)}</b>  <i>({tool}, +{added} −{removed})</i>"

    if tool == "Write" or not old:
        body_lines = new.splitlines() or [""]
        if len(body_lines) > MAX_DIFF_LINES_PER_FILE:
            body_lines = body_lines[:MAX_DIFF_LINES_PER_FILE] + ["…[обрезано]"]
        body = "\n".join("+ " + line for line in body_lines)
        return f"{header}\n<pre>{html_escape(body)}</pre>"

    diff_lines = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            lineterm="",
            n=2,  # контекст 2 строки
        )
    )
    # выкидываем заголовки --- / +++ (мы и так показали имя)
    diff_lines = [line for line in diff_lines if not line.startswith(("---", "+++"))]
    if not diff_lines:
        return f"{header}\n<i>(текст совпал, изменений нет)</i>"
    if len(diff_lines) > MAX_DIFF_LINES_PER_FILE:
        diff_lines = diff_lines[:MAX_DIFF_LINES_PER_FILE] + ["…[обрезано]"]

    body = "\n".join(diff_lines)
    return f"{header}\n<pre>{html_escape(body)}</pre>"


@router.message(Command("diff"))
async def cmd_diff(message: Message):
    if not is_allowed(message):
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0

    with db.conn() as c:
        row = c.execute(
            """SELECT payload FROM events
               WHERE event_type='message_out' AND user_id=? AND chat_id=? AND thread_id=?
               ORDER BY id DESC LIMIT 1""",
            (message.from_user.id, chat_id, thread_id),
        ).fetchone()

    if not row or not row["payload"]:
        await message.answer("Правок в последнем ответе нет.")
        return

    try:
        payload = json.loads(row["payload"])
    except Exception:
        await message.answer("Не удалось распарсить payload последнего ответа.")
        return

    edits = payload.get("edits") or []
    if not edits:
        await message.answer("В последнем ответе правок не было.")
        return

    blocks = []
    total_added = 0
    total_removed = 0
    for edit in edits:
        total_added += edit.get("added", 0)
        total_removed += edit.get("removed", 0)
        blocks.append(_format_diff(edit))

    files_word = "файл" if len(edits) == 1 else ("файла" if 2 <= len(edits) <= 4 else "файлов")
    summary = (
        f"Правки последнего ответа: {len(edits)} {files_word}, "
        f"+{total_added} −{total_removed} строк"
    )
    text = summary + "\n\n" + "\n\n".join(blocks)

    for chunk in split_for_telegram(text, limit=MAX_MESSAGE_CHARS):
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            log.warning("diff send failed (%s), retry without HTML", e)
            await message.answer(chunk)
