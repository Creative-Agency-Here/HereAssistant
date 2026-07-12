"""Скачивание вложений из Telegram в .runtime/downloads/."""

import re
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.types import Message

from core import config


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._\-]+", "_", name)
    return name.strip("_") or "file"


async def download_attachment(bot: Bot, message: Message, user_id: int) -> Optional[Path]:
    """Если в сообщении есть документ/фото/аудио/voice — скачать и вернуть путь.
    Иначе None.
    """
    staging = config.user_downloads(user_id)
    if not staging.exists():
        staging.mkdir(parents=True, exist_ok=True, mode=0o750)

    file_id = None
    suggested_name = None

    if message.document:
        file_id = message.document.file_id
        suggested_name = message.document.file_name or f"doc-{message.document.file_unique_id}"
    elif message.photo:
        # берём самое большое разрешение
        ph = message.photo[-1]
        file_id = ph.file_id
        suggested_name = f"photo-{ph.file_unique_id}.jpg"
    elif message.audio:
        file_id = message.audio.file_id
        suggested_name = message.audio.file_name or f"audio-{message.audio.file_unique_id}.mp3"
    elif message.voice:
        file_id = message.voice.file_id
        suggested_name = f"voice-{message.voice.file_unique_id}.ogg"
    elif message.video:
        file_id = message.video.file_id
        suggested_name = message.video.file_name or f"video-{message.video.file_unique_id}.mp4"
    elif message.video_note:
        file_id = message.video_note.file_id
        suggested_name = f"video_note-{message.video_note.file_unique_id}.mp4"

    if not file_id:
        return None

    ts = time.strftime("%Y%m%d-%H%M%S")
    safe = _safe_name(suggested_name)
    dest = staging / f"{ts}_{safe}"

    try:
        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, destination=str(dest))
        dest.chmod(0o640)
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        return None
