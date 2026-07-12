import pytest

from handlers.message_formatting import format_signature, make_preview, should_skip_edit


def test_make_preview_keeps_short_text() -> None:
    assert make_preview("короткий ответ", 50) == "короткий ответ"


def test_make_preview_prefers_natural_separator() -> None:
    text = "Первый абзац с деталями.\n\nВторой абзац, который уже не помещается."

    assert make_preview(text, 45) == "Первый абзац с деталями.\n\n…"


def test_make_preview_hard_cuts_text_without_separator() -> None:
    assert make_preview("x" * 20, 10) == "x" * 10 + "…"


def test_signature_aggregates_edits_by_file() -> None:
    edits = [
        {"file": "/workspace/app.py", "added": 2, "removed": 1},
        {"file": "/workspace/app.py", "added": 3, "removed": 0},
        {"file": "/workspace/test.py", "added": 1, "removed": 2},
    ]

    signature = format_signature("model", 1.25, edits, updated_at="12:34:56")

    assert signature == (
        "\n\n— model · 1.2с · всего +6 −3 строк · "
        "2 файла: app.py +5/−1, test.py +1/−2 · обновлено 12:34:56"
    )


def test_signature_handles_empty_and_non_integer_edit_values() -> None:
    signature = format_signature(
        None,
        0,
        [{"file": None, "added": "bad", "removed": True}],
        updated_at="00:00:00",
    )

    assert signature == "\n\n— 0.0с · 1 файл: ? +0/−0 · обновлено 00:00:00"


@pytest.mark.parametrize(
    "path",
    [
        "/workspace/.runtime/state.json",
        "/tmp/askpass.sh",
        "C:\\Users\\User\\AppData\\Local\\Temp\\file.txt",
        "/workspace/.env",
        "/workspace/askpass-helper.py",
    ],
)
def test_sensitive_or_temporary_edits_are_skipped(path: str) -> None:
    assert should_skip_edit(path)


def test_regular_source_edit_is_not_skipped() -> None:
    assert not should_skip_edit("/workspace/src/main.py")
