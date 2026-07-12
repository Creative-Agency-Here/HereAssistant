import json
from pathlib import Path
from typing import Any

from providers.gemini import _short_tool_desc
from providers.parsers.gemini import GeminiStreamParser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"


def load_events(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_success_fixture_preserves_delta_text_tools_edits_and_usage() -> None:
    parser = GeminiStreamParser(_short_tool_desc)
    updates = [
        update for event in load_events("gemini_success.jsonl") for update in parser.consume(event)
    ]

    result = parser.provider_result()

    assert updates == ["partial_delta", "partial_delta", "tool_use"]
    assert result.text == "Готово"
    assert result.session_id is None
    assert parser.session_id == "gemini-session"
    assert result.meta.get("tokens_in") == 40
    assert result.meta.get("tokens_out") == 7
    assert result.meta.get("tool_uses") == ["write_file"]
    assert result.meta.get("tool_call_log") == ["Write result.txt"]
    assert result.meta.get("edits") == [
        {
            "tool": "write_file",
            "file": "/workspace/result.txt",
            "added": 2,
            "removed": 0,
            "old": "",
            "new": "one\ntwo",
        }
    ]
    assert parser.current_tool is None


def test_full_assistant_message_replaces_previous_text() -> None:
    parser = GeminiStreamParser(_short_tool_desc)
    parser.consume({"type": "message", "role": "assistant", "delta": True, "content": "old"})

    parser.consume({"type": "message", "role": "assistant", "content": "full"})

    assert parser.text == "full"


def test_result_text_is_fallback_only() -> None:
    empty = GeminiStreamParser(_short_tool_desc)
    filled = GeminiStreamParser(_short_tool_desc)
    filled.text = "streamed"

    empty.consume({"type": "result", "response": "fallback"})
    filled.consume({"type": "result", "response": "must not replace"})

    assert empty.provider_result().text == "fallback"
    assert filled.provider_result().text == "streamed"


def test_tool_call_deduplicates_by_id_and_updates_description() -> None:
    parser = GeminiStreamParser(_short_tool_desc)
    parser.consume({"type": "tool_use", "tool_id": "same", "tool_name": "read_file"})
    parser.consume(
        {
            "type": "tool_use",
            "tool_id": "same",
            "tool_name": "read_file",
            "parameters": {"file_path": "/workspace/full.py"},
        }
    )

    assert parser.tool_call_log == ["Read full.py"]
    # tool_uses отражает реальные события, а не уникальные описания.
    assert parser.tool_uses == ["read_file", "read_file"]


def test_invalid_parameters_and_unknown_events_are_safe() -> None:
    parser = GeminiStreamParser(_short_tool_desc)

    assert parser.consume({"type": "tool_use", "name": "custom", "parameters": "bad"}) == [
        "tool_use"
    ]
    assert parser.consume({"type": "future", "payload": []}) == []
    assert parser.tool_call_log == ["custom"]
    assert parser.events_seen == {"tool_use": 1, "future": 1}


def test_progress_meta_keeps_common_cross_provider_shape() -> None:
    parser = GeminiStreamParser(_short_tool_desc)

    assert parser.progress_meta() == {
        "edits": [],
        "tool_uses": [],
        "tool_call_log": [],
        "steps": [],
        "thinking": "",
        "current_tool": None,
    }
