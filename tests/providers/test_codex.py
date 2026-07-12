from pathlib import Path

import pytest

from providers.codex import _extract_session_id
from providers.parsers.codex import extract_session_id

FIXTURE = Path(__file__).parents[1] / "fixtures" / "providers" / "codex_session.txt"


@pytest.mark.parametrize(
    ("stdout", "stderr", "current", "expected"),
    [
        ("", "", None, None),
        ("обычный ответ", "", "existing-session-id", "existing-session-id"),
        (
            "session id: 12345678-abcd-4321-aaaa-123456789012",
            "",
            None,
            "12345678-abcd-4321-aaaa-123456789012",
        ),
        (
            "",
            "Session ID, 'abcdef12-3456-7890-abcd-ef1234567890'",
            None,
            "abcdef12-3456-7890-abcd-ef1234567890",
        ),
        ("session mentioned without id", "", "keep-me-session", "keep-me-session"),
        ("id only 12345678-abcd-4321", "", None, None),
    ],
)
def test_extract_session_id(
    stdout: str, stderr: str, current: str | None, expected: str | None
) -> None:
    assert _extract_session_id(stdout, stderr, current) == expected


def test_codex_text_fixture_extracts_anonymized_session() -> None:
    text = FIXTURE.read_text(encoding="utf-8")

    assert extract_session_id(text, "") == "12345678-abcd-4321-aaaa-123456789012"
    assert "token" not in text.lower()
