import logging
from pathlib import Path
from typing import Any

import pytest

from handlers.message_attachments import prepare_message_input


class FakeStatusMessage:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text: str) -> None:
        self.edits.append(text)


class FakeMessage:
    def __init__(self) -> None:
        self.answers: list[str] = []
        self.statuses: list[FakeStatusMessage] = []

    async def answer(self, text: str) -> FakeStatusMessage:
        self.answers.append(text)
        status = FakeStatusMessage()
        self.statuses.append(status)
        return status


@pytest.mark.asyncio
async def test_regular_attachments_keep_first_as_main_and_list_extras(tmp_path: Path) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "two.png"
    message: Any = FakeMessage()

    prepared = await prepare_message_input(
        message,
        ["обработай файлы"],
        [first, second],
        logger=logging.getLogger("test"),
        is_voice_file=lambda _path: False,
    )

    assert prepared.main_attachment == first
    assert prepared.attachments == [first, second]
    assert prepared.text == f"обработай файлы\n\n[Доп. вложения]\n- {second}"


@pytest.mark.asyncio
async def test_files_without_text_get_explicit_prompt(tmp_path: Path) -> None:
    attachment = tmp_path / "brief.pdf"
    message: Any = FakeMessage()

    prepared = await prepare_message_input(
        message,
        [],
        [attachment],
        logger=logging.getLogger("test"),
        is_voice_file=lambda _path: False,
    )

    assert prepared.text == "(пользователь прислал файлы: brief.pdf)"
    assert prepared.main_attachment == attachment


@pytest.mark.asyncio
async def test_successful_voice_is_transcribed_and_removed_from_attachments(tmp_path: Path) -> None:
    voice = tmp_path / "voice.ogg"
    message = FakeMessage()

    async def transcribe(_path: Path) -> str:
        return "текст голосового"

    prepared = await prepare_message_input(
        message,  # type: ignore[arg-type]
        ["контекст"],
        [voice],
        logger=logging.getLogger("test"),
        is_voice_file=lambda _path: True,
        transcribe=transcribe,
    )

    assert prepared.text == "контекст\nтекст голосового"
    assert prepared.main_attachment is None
    assert prepared.attachments == []
    assert message.answers == ["🎙 расшифровываю голосовое…"]
    assert message.statuses[0].edits == ["🎙 расшифровано:\nтекст голосового"]


@pytest.mark.asyncio
async def test_failed_voice_keeps_original_file_for_cli(tmp_path: Path) -> None:
    voice = tmp_path / "voice.ogg"
    message = FakeMessage()

    async def fail(_path: Path) -> str:
        raise RuntimeError("model unavailable")

    prepared = await prepare_message_input(
        message,  # type: ignore[arg-type]
        [],
        [voice],
        logger=logging.getLogger("test"),
        is_voice_file=lambda _path: True,
        transcribe=fail,
    )

    assert prepared.text == "[не удалось расшифровать voice.ogg]"
    assert prepared.main_attachment == voice
    assert prepared.attachments == [voice]
    assert message.statuses[0].edits == ["❌ Whisper упал: RuntimeError"]


@pytest.mark.asyncio
async def test_long_transcript_status_is_bounded(tmp_path: Path) -> None:
    voice = tmp_path / "voice.ogg"
    message = FakeMessage()

    async def transcribe(_path: Path) -> str:
        return "x" * 4000

    await prepare_message_input(
        message,  # type: ignore[arg-type]
        [],
        [voice],
        logger=logging.getLogger("test"),
        is_voice_file=lambda _path: True,
        transcribe=transcribe,
    )

    status = message.statuses[0].edits[0]
    assert status.startswith("🎙 расшифровано:\n")
    assert status.endswith("…(расшифровка обрезана по лимиту Telegram)")
    assert len(status) < 4000
