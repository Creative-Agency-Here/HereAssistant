"""Транскрипция голосовых/аудио через faster-whisper.

Модель грузится лениво один раз и держится в памяти. Запуск
heavy compute идёт в отдельном потоке через asyncio.to_thread,
чтобы не блокировать event loop.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("bridge.whisper")

# Размер модели: tiny ~75MB, base ~150MB, small ~500MB, medium ~1.5GB, large-v3 ~3GB.
# small — разумный дефолт для русского на CPU.
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")  # int8 — быстро на CPU

_model = None
_load_lock = asyncio.Lock()


async def _get_model():
    global _model
    if _model is not None:
        return _model
    async with _load_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel

        log.info("loading whisper model=%s device=%s compute=%s", MODEL_SIZE, DEVICE, COMPUTE_TYPE)
        # первая загрузка скачает модель с HuggingFace (~250MB для small)
        _model = await asyncio.to_thread(
            WhisperModel,
            MODEL_SIZE,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
        )
        log.info("whisper model ready")
        return _model


async def transcribe(path: Path, language: Optional[str] = None) -> str:
    """Расшифровать аудио. Возвращает склеенный текст всех сегментов."""
    model = await _get_model()

    def _do():
        segments, _info = model.transcribe(
            str(path),
            language=language,  # None = автодетект
            beam_size=5,
            vad_filter=True,  # вырезает паузы — точнее на войсах
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    text = await asyncio.to_thread(_do)
    log.info("transcribed %s | chars=%d", path.name, len(text))
    return text


VOICE_EXTS = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".webm"}


def is_voice_file(path: Path) -> bool:
    return path.suffix.lower() in VOICE_EXTS
