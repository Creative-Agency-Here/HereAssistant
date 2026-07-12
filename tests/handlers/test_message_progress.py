from pathlib import Path
from typing import cast

from aiogram.types import Message

from handlers.message_progress import (
    ProgressRenderContext,
    ProgressState,
    activate_quiet_mode,
    apply_flood_backoff,
    can_push_progress,
    record_push_success,
    render_progress,
)


def context(**overrides: object) -> ProgressRenderContext:
    values = {
        "model": "model<x>",
        "account_label": "main&team",
        "account_notes": None,
        "started_at": 100.0,
        "chain_limit": 2,
        "max_partial_chars": 20,
        "draft_enabled": False,
    }
    values.update(overrides)
    return ProgressRenderContext(**values)  # type: ignore[arg-type]


def test_header_escapes_values_and_switches_seconds_to_minutes() -> None:
    state = ProgressState(last_meta={"current_tool": "Read<x>"})

    rendered = render_progress(state, context(), now=165.0)

    assert rendered.html.startswith(
        "🤖 model&lt;x&gt; · 👤 main&amp;team · ⌛ 1 мин · 🔧 Read&lt;x&gt;"
    )
    assert rendered.html.endswith("💭 думаю…")


def test_attachments_have_icons_and_visible_limit() -> None:
    state = ProgressState(
        attachments=[
            Path("photo.png"),
            Path("video.mp4"),
            Path("voice.ogg"),
            Path("brief.pdf"),
            Path("code.py"),
            Path("extra.txt"),
        ]
    )

    html = render_progress(state, context(), now=101).html

    assert "📷 photo.png" in html
    assert "🎬 video.mp4" in html
    assert "🎵 voice.ogg" in html
    assert "📄 brief.pdf" in html
    assert "📎 code.py" in html
    assert "… ещё 1" in html
    assert "extra.txt" not in html


def test_structured_steps_show_status_result_and_tail_limit() -> None:
    state = ProgressState(
        last_meta={
            "steps": [
                {"status": "ok", "desc": "first"},
                {"status": "err", "desc": "second<x>", "result": "failed&reason"},
                {"status": "run", "desc": "third"},
            ]
        }
    )

    html = render_progress(state, context(), now=101).html

    assert "показано 2 из 3" in html
    assert "2. ✗ second&lt;x&gt;" in html
    assert "failed&amp;reason" in html
    assert "3. ⏳ third" in html
    assert "first" not in html


def test_flat_tool_log_is_used_as_fallback() -> None:
    state = ProgressState(last_meta={"tool_call_log": ["Read a", "Write b"]})

    html = render_progress(state, context(), now=101).html

    assert "📋 Шаги (2)" in html
    assert "1. Read a" in html
    assert "2. Write b" in html


def test_long_partial_marks_overflow_and_keeps_bounded_tail() -> None:
    state = ProgressState(last_partial="prefix-" + "x" * 30)

    rendered = render_progress(state, context(), now=101)

    assert rendered.overflowed
    assert "продолжаю, финал придёт отдельно" in rendered.html
    assert len(rendered.html) < 4096


def test_draft_suppresses_partial_but_keeps_thinking() -> None:
    state = ProgressState(last_partial="streamed", last_meta={"thinking": "проверяю"})

    html = render_progress(state, context(draft_enabled=True), now=101).html

    assert "streamed" not in html
    assert "Размышляет" in html
    assert "проверяю" in html


def test_quiet_mode_marker_is_rendered() -> None:
    state = ProgressState(quiet_mode=True)

    assert "Работаю молча" in render_progress(state, context(), now=101).html


def test_quiet_mode_activates_only_after_threshold() -> None:
    state = ProgressState()

    assert not activate_quiet_mode(state, now=700, started_at=100, after_seconds=600)
    assert activate_quiet_mode(state, now=700.1, started_at=100, after_seconds=600)
    assert state.quiet_mode
    assert not activate_quiet_mode(state, now=800, started_at=100, after_seconds=600)


def test_force_bypasses_interval_but_never_flood_cooldown() -> None:
    state = ProgressState(message=cast(Message, object()), last_edit_ts=100, min_interval=10)

    assert not can_push_progress(state, now=105, quiet_interval=30)
    assert can_push_progress(state, now=105, quiet_interval=30, force=True)

    state.cooldown_until = 120
    assert not can_push_progress(state, now=105, quiet_interval=30, force=True)


def test_quiet_mode_uses_the_larger_interval() -> None:
    state = ProgressState(
        message=cast(Message, object()),
        last_edit_ts=100,
        min_interval=10,
        quiet_mode=True,
    )

    assert not can_push_progress(state, now=129.9, quiet_interval=30)
    assert can_push_progress(state, now=130, quiet_interval=30)


def test_flood_backoff_caps_interval_and_resets_successes() -> None:
    state = ProgressState(min_interval=10, success_streak=4)

    apply_flood_backoff(
        state,
        now=100,
        wait_seconds=20,
        factor=2,
        max_interval=15,
    )

    assert state.cooldown_until == 121
    assert state.min_interval == 15
    assert state.success_streak == 0


def test_success_streak_gradually_restores_base_interval() -> None:
    state = ProgressState(min_interval=12, success_streak=1)

    record_push_success(state, base_interval=3, factor=2, reset_after=3)
    assert state.min_interval == 12
    assert state.success_streak == 2

    record_push_success(state, base_interval=3, factor=2, reset_after=3)
    assert state.min_interval == 6
    assert state.success_streak == 0
