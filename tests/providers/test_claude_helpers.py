import pytest

from providers.claude_code import (
    _extract_text_from_block,
    _extract_text_from_message,
    _extract_thinking,
    _result_preview,
    _short_tool_desc,
)


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"type": "text", "text": "готово"}, "готово"),
        ({"type": "text_delta", "text": "часть"}, "часть"),
        ({"type": "text_delta", "delta": "дельта"}, "дельта"),
        ({"type": "tool_use", "text": "не ответ"}, ""),
        ("не словарь", ""),
    ],
)
def test_extract_text_from_block(block: object, expected: str) -> None:
    assert _extract_text_from_block(block) == expected  # type: ignore[arg-type]


def test_extract_text_from_message_supports_string_and_blocks() -> None:
    assert _extract_text_from_message({"content": "цельный ответ"}) == "цельный ответ"
    assert (
        _extract_text_from_message(
            {
                "content": [
                    {"type": "text", "text": "первая"},
                    {"type": "tool_use", "name": "Read"},
                    {"type": "text", "text": " вторая"},
                ]
            }
        )
        == "первая вторая"
    )
    assert _extract_text_from_message({"content": None}) == ""


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"type": "thinking", "thinking": "проверяю"}, "проверяю"),
        ({"type": "thinking_delta", "thinking": "шаг"}, "шаг"),
        ({"type": "thinking_delta", "delta": "дельта"}, "дельта"),
        ({"type": "text", "text": "ответ"}, ""),
    ],
)
def test_extract_thinking(block: dict[str, str], expected: str) -> None:
    assert _extract_thinking(block) == expected


def test_result_preview_uses_first_non_empty_line_without_leaking_full_output() -> None:
    content = "\nпервая строка\nвторая строка\nтретья строка"

    assert _result_preview(content) == "первая строка (+2 стр.)"
    assert _result_preview(content, limit=6) == "первая (+2 стр.)"


def test_result_preview_supports_content_blocks_and_empty_values() -> None:
    assert _result_preview([{"type": "text", "text": "результат"}, "ещё"]) == (
        "результат (+1 стр.)"
    )
    assert _result_preview(None) == ""
    assert _result_preview([]) == ""


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("Read", {"file_path": "/tmp/project/main.py"}, "Read main.py"),
        ("Edit", {"filePath": "/tmp/project/app.ts"}, "Edit app.ts"),
        ("Glob", {"pattern": "src/**/*.py"}, "Glob src/**/*.py"),
        ("Grep", {"pattern": "privacy"}, "Grep 'privacy'"),
        ("Bash", {"command": "pytest -q"}, "Bash: pytest -q"),
        ("TaskUpdate", {"taskId": "7", "status": "done"}, "TaskUpdate #7 → done"),
        ("WebFetch", {"url": "https://example.com"}, "WebFetch https://example.com"),
        ("Skill", {"skill": "review"}, "Skill /review"),
        ("Custom", {"value": "детали"}, "Custom: детали"),
        ("Custom", {}, "Custom"),
    ],
)
def test_short_tool_description(name: str, payload: dict[str, str], expected: str) -> None:
    assert _short_tool_desc(name, payload) == expected


def test_short_tool_description_truncates_long_shell_command() -> None:
    description = _short_tool_desc("Bash", {"command": "x" * 200})

    assert description.startswith("Bash: ")
    assert description.endswith("…")
    assert len(description) < 100
