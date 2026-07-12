import json
from types import SimpleNamespace

import pytest

from utils import rich


def test_feature_flags_respect_global_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rich, "RICH_MESSAGES", True)
    monkeypatch.setattr(rich, "RICH_STREAM", True)
    monkeypatch.setattr(rich, "_available", True)

    assert rich.enabled()
    assert rich.stream_enabled()

    monkeypatch.setattr(rich, "_available", False)
    assert not rich.enabled()
    assert not rich.stream_enabled()


def test_stream_flag_can_be_disabled_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rich, "RICH_MESSAGES", True)
    monkeypatch.setattr(rich, "RICH_STREAM", False)
    monkeypatch.setattr(rich, "_available", True)

    assert rich.enabled()
    assert not rich.stream_enabled()


def test_markdown_sanity_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rich, "RICH_TEXT_LIMIT", 5)

    assert rich.sanity_check_markdown("тест")
    assert not rich.sanity_check_markdown("")
    assert not rich.sanity_check_markdown("   ")
    assert not rich.sanity_check_markdown("слишком длинно")


def test_debug_dump_contains_only_shape_not_content() -> None:
    secret = "PRIVATE_PROMPT"

    dumped = rich.debug_dump(f"line one\n{secret}")
    payload = json.loads(dumped)

    assert payload == {"len": len(f"line one\n{secret}"), "lines": 2}
    assert secret not in dumped


@pytest.mark.asyncio
async def test_send_message_builds_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str, dict[str, object]]] = []

    async def fake_call(bot: object, method: str, payload: dict[str, object]) -> dict[str, int]:
        calls.append((bot, method, payload))
        return {"message_id": 7}

    monkeypatch.setattr(rich, "_call", fake_call)
    bot = SimpleNamespace(token="test")

    result = await rich.send_message(bot, 42, "# Ответ", thread_id=9)

    assert result == {"message_id": 7}
    assert calls == [
        (
            bot,
            "sendRichMessage",
            {
                "chat_id": 42,
                "rich_message": {"markdown": "# Ответ"},
                "message_thread_id": 9,
            },
        )
    ]


@pytest.mark.asyncio
async def test_send_draft_returns_boolean_result(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter([{"ok": True}, None])

    async def fake_call(_bot: object, _method: str, _payload: dict[str, object]) -> object:
        return next(results)

    monkeypatch.setattr(rich, "_call", fake_call)
    bot = SimpleNamespace(token="test")

    assert await rich.send_draft(bot, 42, 1, "partial")
    assert not await rich.send_draft(bot, 42, 2, "partial")
