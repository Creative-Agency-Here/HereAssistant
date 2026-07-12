"""Подготовка вложений и voice transcript перед запуском CLI-провайдера."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from aiogram.types import Message

from utils import whisper

VoiceDetector = Callable[[Path], bool]
Transcriber = Callable[[Path], Awaitable[str]]


@dataclass(slots=True)
class PreparedMessageInput:
    text: str
    main_attachment: Path | None
    attachments: list[Path]


async def prepare_message_input(
    message: Message,
    texts: Sequence[str],
    attachments: Sequence[Path],
    *,
    logger: logging.Logger,
    is_voice_file: VoiceDetector = whisper.is_voice_file,
    transcribe: Transcriber = whisper.transcribe,
) -> PreparedMessageInput:
    transcripts: list[str] = []
    remaining: list[Path] = []

    for attachment in attachments:
        if not is_voice_file(attachment):
            remaining.append(attachment)
            continue

        status_message = await _try_send_status(message)
        try:
            transcript = await transcribe(attachment)
            transcripts.append(transcript)
            if status_message:
                await _try_edit_status(
                    status_message,
                    "🎙 расшифровано:\n" + _transcript_preview(transcript),
                )
        except Exception as error:
            logger.warning("whisper failed for %s: %s", attachment, error)
            transcripts.append(f"[не удалось расшифровать {attachment.name}]")
            if status_message:
                await _try_edit_status(
                    status_message,
                    f"❌ Whisper упал: {type(error).__name__}",
                )
            # Не теряем исходный файл: CLI сможет обработать его как обычное вложение.
            remaining.append(attachment)

    user_text = "\n".join(texts) if texts else ""
    if transcripts:
        joined = "\n".join(transcripts)
        user_text = (user_text + "\n" + joined).strip() if user_text else joined
    if not user_text and remaining:
        names = ", ".join(attachment.name for attachment in remaining)
        user_text = f"(пользователь прислал файлы: {names})"

    main_attachment = remaining[0] if remaining else None
    if len(remaining) > 1:
        extra = "\n".join(f"- {attachment}" for attachment in remaining[1:])
        user_text += f"\n\n[Доп. вложения]\n{extra}"

    return PreparedMessageInput(user_text, main_attachment, remaining)


async def _try_send_status(message: Message) -> Message | None:
    try:
        return await message.answer("🎙 расшифровываю голосовое…")
    except Exception:
        return None


async def _try_edit_status(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except Exception:
        pass


def _transcript_preview(text: str, limit: int = 3900) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…(расшифровка обрезана по лимиту Telegram)"
