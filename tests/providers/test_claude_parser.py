import json
from pathlib import Path
from typing import Any

from providers.claude_code import (
    _extract_text_from_message,
    _extract_thinking,
    _result_preview,
    _short_tool_desc,
)
from providers.parsers.claude import ClaudeStreamParser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"


def parser(session_id: str | None = None) -> ClaudeStreamParser:
    return ClaudeStreamParser(
        text_from_message=_extract_text_from_message,
        thinking_from_block=_extract_thinking,
        result_preview=_result_preview,
        tool_description=_short_tool_desc,
        session_id=session_id,
    )


def load_events(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_success_fixture_preserves_text_tools_edits_steps_and_usage() -> None:
    stream = parser()
    updates = [
        update for event in load_events("claude_success.jsonl") for update in stream.consume(event)
    ]

    result = stream.provider_result()

    assert updates == ["thinking_delta", "tool_start", "tool_result", "partial_delta"]
    assert result.text == "Готово"
    assert result.session_id == "session-final"
    assert result.meta.get("tokens_in") == 120
    assert result.meta.get("tokens_out") == 15
    assert result.meta.get("tool_uses") == ["Edit"]
    assert result.meta.get("tool_call_log") == ["Edit example.py"]
    assert result.meta.get("edits") == [
        {
            "tool": "Edit",
            "file": "/workspace/example.py",
            "added": 2,
            "removed": 1,
            "old": "old",
            "new": "new\nline",
        }
    ]
    assert result.meta.get("steps") == [
        {
            "id": "tool-1",
            "name": "Edit",
            "desc": "Edit example.py",
            "status": "ok",
            "result": "Файл изменён (+1 стр.)",
        }
    ]
    assert stream.thinking == "Проверяю план"
    assert stream.current_tool is None


def test_tool_call_is_deduplicated_and_updated_by_full_assistant_input() -> None:
    stream = parser()
    stream.consume(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "id": "same", "name": "Read"},
            },
        }
    )
    stream.consume(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "same",
                        "name": "Read",
                        "input": {"file_path": "/workspace/full.py"},
                    }
                ]
            },
        }
    )

    assert stream.tool_call_log == ["Read full.py"]
    assert len(stream.steps) == 1
    assert stream.steps[0].desc == "Read full.py"


def test_assistant_full_text_does_not_duplicate_partial_text() -> None:
    stream = parser()
    stream.consume(
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"text": "Ответ"}},
        }
    )

    assert stream.consume({"type": "assistant", "message": {"content": "Ответ"}}) == [
        "assistant_delta"
    ]
    assert stream.text == "Ответ"


def test_error_result_builds_human_reason_without_exposing_unbounded_text() -> None:
    stream = parser("old-session")
    stream.consume(
        {
            "type": "result",
            "is_error": True,
            "subtype": "authentication_failed",
            "result": "x" * 800,
        }
    )

    assert stream.error_subtype == "authentication_failed"
    assert stream.error_reason("") == "x" * 500
    assert stream.error_reason("stderr details") == "stderr details"


def test_partial_rate_limit_returns_accumulated_text_and_metadata() -> None:
    stream = parser()
    stream.text = "Частичный ответ"
    stream.consume(
        {
            "type": "rate_limit_event",
            "subtype": "exceeded",
            "rate_limit": {"resets_at": "12:30"},
        }
    )

    result = stream.partial_rate_limit_result()

    assert result.text.startswith("Частичный ответ")
    assert "12:30" in result.text
    assert result.meta.get("rate_limit_hits") == 1
    assert result.meta.get("rate_limit_reset") == "12:30"
    assert result.meta.get("partial_due_to_error") is True


def test_unknown_and_orphan_tool_result_events_are_safe() -> None:
    stream = parser()

    assert stream.consume({"type": "future_event", "payload": {"x": 1}}) == []
    assert stream.consume({"type": "tool_result", "id": "missing", "content": "ok"}) == [
        "tool_result"
    ]
    assert stream.steps == []
    assert stream.events_seen == {"future_event": 1, "tool_result": 1}
