from io import StringIO

import pytest

from chat_renderer import (
    MdStream,
    ProgressRenderState,
    finish_stream,
    format_run_summary,
    make_progress,
    terminal_text,
)


def test_markdown_tokens_split_between_chunks_do_not_leak() -> None:
    renderer = MdStream()

    output = renderer.feed("**bo") + renderer.feed("ld**") + renderer.close()

    assert "**" not in output
    assert "bold" in output


def test_unfinished_single_marker_is_flushed_literally_on_close() -> None:
    renderer = MdStream()

    assert renderer.feed("text*") == "text"
    assert renderer.close().endswith("*")


def test_terminal_text_preserves_copyable_lines_but_removes_control_bytes() -> None:
    value = terminal_text("first\r\nsecond\rthird\x1b]0;hijack\x07")

    assert value == "first\nsecond\nthird]0;hijack"
    assert "\x1b" not in value
    assert "\x07" not in value


def test_headings_lists_inline_code_and_fences_are_rendered() -> None:
    renderer = MdStream()
    markdown = "# Header\n- item\n`code`\n```py\nprint(1)\n```\n"

    output = renderer.feed(markdown) + renderer.close()

    assert "# Header" not in output
    assert "Header" in output
    assert "• item" in output
    assert "`code`" not in output
    assert "code" in output
    assert "```" not in output
    assert "print(1)" in output


@pytest.mark.asyncio
async def test_progress_prints_only_new_thinking_step_and_text_deltas() -> None:
    state = ProgressRenderState()
    output = StringIO()
    progress = make_progress(state, output=output)
    step = {"id": "tool-1", "status": "ok", "desc": "Read file", "result": "done"}

    await progress("hello", "assistant_delta", {"thinking": "abc", "steps": [step]})
    await progress("hello world", "assistant_delta", {"thinking": "abcde", "steps": [step]})

    rendered = output.getvalue()
    assert "💭 abc" in rendered
    assert "abcde" not in rendered
    assert rendered.count("Read file") == 1
    assert rendered.count("⎿ done") == 1
    assert rendered.count("hello") == 1
    assert "hello world" in rendered


@pytest.mark.asyncio
async def test_provider_text_reset_starts_a_new_paragraph() -> None:
    state = ProgressRenderState()
    output = StringIO()
    progress = make_progress(state, output=output)

    await progress("first", "assistant_delta", {})
    await progress("replacement", "assistant_delta", {})

    assert "first\nreplacement" in output.getvalue()
    assert state.text_prefix == "replacement"


def test_finish_stream_handles_non_streaming_final_answer() -> None:
    state = ProgressRenderState()
    output = StringIO()

    finish_stream(state, "**final**", output=output)

    assert "final" in output.getvalue()
    assert "**" not in output.getvalue()
    assert state.answer_started


def test_run_summary_aggregates_edits_and_tokens_safely() -> None:
    summary = format_run_summary(
        {
            "edits": [
                {"added": 3, "removed": 1},
                {"added": 2, "removed": True},
            ],
            "tokens_in": 10,
            "tokens_out": 20,
        },
        12.6,
    )

    assert "13с" in summary
    assert "+5" in summary
    assert "−1" in summary
    assert "2 файл." in summary
    assert "токены 10/20" in summary
