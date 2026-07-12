from typing import Any

import pytest

from handlers.common import send_long


class FakeMessage:
    def __init__(self, *, fail_html_once: bool = False) -> None:
        self.fail_html_once = fail_html_once
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.calls.append((text, kwargs))
        if self.fail_html_once and kwargs.get("parse_mode") == "HTML":
            self.fail_html_once = False
            raise ValueError("broken HTML")


@pytest.mark.asyncio
async def test_send_long_converts_markdown_to_html() -> None:
    message = FakeMessage()

    await send_long(message, "**готово**")  # type: ignore[arg-type]

    assert message.calls == [("<b>готово</b>", {"parse_mode": "HTML"})]


@pytest.mark.asyncio
async def test_send_long_uses_empty_placeholder() -> None:
    message = FakeMessage()

    await send_long(message, "")  # type: ignore[arg-type]

    assert message.calls == [("(пусто)", {"parse_mode": "HTML"})]


@pytest.mark.asyncio
async def test_send_long_does_not_convert_preformatted_html() -> None:
    message = FakeMessage()

    await send_long(message, "<b>готово</b>", html_already=True)  # type: ignore[arg-type]

    assert message.calls == [("<b>готово</b>", {"parse_mode": "HTML"})]


@pytest.mark.asyncio
async def test_send_long_falls_back_to_plain_text_after_html_error() -> None:
    message = FakeMessage(fail_html_once=True)

    await send_long(message, "**готово**")  # type: ignore[arg-type]

    assert message.calls == [
        ("<b>готово</b>", {"parse_mode": "HTML"}),
        ("<b>готово</b>", {}),
    ]
