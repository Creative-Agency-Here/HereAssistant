"""Типизированное состояние и чистый HTML-render progress-сообщения."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiogram.types import Message

from utils.markdown import html_escape, markdown_to_html

STEP_ICONS = {"run": "⏳", "ok": "✓", "err": "✗"}
HTML_LIMIT = 4096
SAFE_HTML_LIMIT = 3800


@dataclass(slots=True)
class ProgressState:
    message: Message | None = None
    last_partial: str = ""
    last_meta: Mapping[str, Any] = field(default_factory=dict)
    last_displayed: str = ""
    last_edit_ts: float = 0.0
    last_event_ts: float = 0.0
    overflowed: bool = False
    cooldown_until: float = 0.0
    min_interval: float = 1.5
    success_streak: int = 0
    quiet_mode: bool = False
    attachments: list[Path] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProgressRenderContext:
    model: str | None
    account_label: str | None
    account_notes: str | None
    started_at: float
    chain_limit: int
    max_partial_chars: int
    draft_enabled: bool


@dataclass(frozen=True, slots=True)
class ProgressRender:
    html: str
    overflowed: bool = False


def activate_quiet_mode(
    state: ProgressState,
    *,
    now: float,
    started_at: float,
    after_seconds: float,
) -> bool:
    """Включает quiet mode один раз и сообщает о переходе состояния."""
    if state.quiet_mode or now - started_at <= after_seconds:
        return False
    state.quiet_mode = True
    return True


def can_push_progress(
    state: ProgressState,
    *,
    now: float,
    quiet_interval: float,
    force: bool = False,
) -> bool:
    """Проверяет cooldown и throttling; force не обходит Telegram cooldown."""
    if now < state.cooldown_until:
        return False
    if state.message is None or force:
        return True
    interval = state.min_interval
    if state.quiet_mode:
        interval = max(interval, quiet_interval)
    return now - state.last_edit_ts >= interval


def apply_flood_backoff(
    state: ProgressState,
    *,
    now: float,
    wait_seconds: float,
    factor: float,
    max_interval: float,
) -> None:
    """Фиксирует Telegram cooldown и увеличивает интервал следующих edit."""
    state.cooldown_until = now + wait_seconds + 1.0
    state.min_interval = min(state.min_interval * factor, max_interval)
    state.success_streak = 0


def record_push_success(
    state: ProgressState,
    *,
    base_interval: float,
    factor: float,
    reset_after: int,
) -> None:
    """После серии успешных edit постепенно возвращает базовый интервал."""
    state.success_streak += 1
    if state.success_streak < reset_after or state.min_interval <= base_interval:
        return
    state.min_interval = max(base_interval, state.min_interval / factor)
    state.success_streak = 0


def render_progress(
    state: ProgressState,
    context: ProgressRenderContext,
    *,
    now: float,
) -> ProgressRender:
    elapsed = max(0, int(now - context.started_at))
    meta = state.last_meta
    current_tool = meta.get("current_tool")
    chain = _sequence(meta.get("tool_call_log"))

    head: list[str] = []
    if context.model:
        head.append(f"🤖 {html_escape(context.model)}")
    if context.account_label:
        head.append(f"👤 {html_escape(context.account_label)}")
    if context.account_notes:
        head.append(f"📝 {html_escape(context.account_notes)}")
    head.append(f"⌛ {elapsed // 60} мин" if elapsed >= 60 else f"⌛ {elapsed}с")
    if current_tool:
        head.append(f"🔧 {html_escape(str(current_tool))}")
    parts = [" · ".join(head)]

    if state.quiet_mode:
        parts.append(
            "🔇 <i>Работаю молча — задача длинная, прогресс приглушён, "
            "чтобы не упереться в лимит Telegram. Финальный ответ придёт отдельно.</i>"
        )
    attachments = _render_attachments(state.attachments)
    if attachments:
        parts.append(attachments)

    chain_part = _render_steps(meta.get("steps"), chain, context.chain_limit)
    raw_partial = "" if context.draft_enabled else state.last_partial
    partial_html = ""
    overflowed = False
    if raw_partial:
        if len(raw_partial) > context.max_partial_chars:
            overflowed = True
            partial_html = (
                markdown_to_html(raw_partial[-context.max_partial_chars :])
                + "\n…⏳ продолжаю, финал придёт отдельно"
            )
        else:
            partial_html = markdown_to_html(raw_partial)

    thinking_part = _render_thinking(meta.get("thinking"), bool(raw_partial))
    fixed = "\n\n".join(parts)
    if chain_part:
        fixed += "\n\n" + chain_part
    if thinking_part and len(fixed) + len(thinking_part) + 2 < SAFE_HTML_LIMIT:
        fixed += "\n\n" + thinking_part

    if not chain_part and not raw_partial:
        return ProgressRender(fixed + ("" if thinking_part else "\n\n💭 думаю…"), overflowed)

    if partial_html:
        budget = SAFE_HTML_LIMIT - len(fixed) - 2
        if budget < 150:
            return ProgressRender(fixed, overflowed)
        if len(partial_html) > budget:
            partial_html = markdown_to_html(raw_partial[-budget:])
            if len(fixed) + 2 + len(partial_html) > HTML_LIMIT:
                return ProgressRender(fixed, overflowed)
        fixed += "\n\n" + partial_html
    return ProgressRender(fixed, overflowed)


def _render_attachments(attachments: Sequence[Path]) -> str:
    if not attachments:
        return ""
    lines = [f"{_attachment_icon(path.name)} {html_escape(path.name)}" for path in attachments[:5]]
    if len(attachments) > 5:
        lines.append(f"… ещё {len(attachments) - 5}")
    return "Получено:\n" + "\n".join(lines)


def _attachment_icon(name: str) -> str:
    lower = name.lower()
    if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
        return "📷"
    if lower.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi")):
        return "🎬"
    if lower.endswith((".mp3", ".ogg", ".oga", ".m4a", ".wav", ".flac")):
        return "🎵"
    if lower.endswith(".pdf"):
        return "📄"
    return "📎"


def _render_steps(raw_steps: object, chain: Sequence[object], limit: int) -> str:
    steps = _sequence(raw_steps)
    if steps:
        total = len(steps)
        start = max(0, total - limit)
        shown = steps[start:]
        lines = [f"… (показано {len(shown)} из {total})"] if start else []
        for index, raw_step in enumerate(shown, start + 1):
            step = raw_step if isinstance(raw_step, Mapping) else {}
            icon = STEP_ICONS.get(str(step.get("status")), "•")
            description = str(step.get("desc") or step.get("name") or "?")
            if len(description) > 200:
                description = description[:200] + "…"
            lines.append(f"{index}. {icon} {html_escape(description)}")
            result = step.get("result")
            if result:
                preview = str(result)
                if len(preview) > 160:
                    preview = preview[:160] + "…"
                lines.append(f"    ⎿ <i>{html_escape(preview)}</i>")
        body = "\n".join(lines)
        return f"📋 Шаги ({total})\n<blockquote expandable>{body}</blockquote>"

    if not chain:
        return ""
    total = len(chain)
    start = max(0, total - limit)
    shown = chain[start:]
    lines = [f"… (показано {len(shown)} из {total})"] if start else []
    for index, item in enumerate(shown, start + 1):
        description = str(item)
        if len(description) > 200:
            description = description[:200] + "…"
        lines.append(f"{index}. {html_escape(description)}")
    body = "\n".join(lines)
    return f"📋 Шаги ({total})\n<blockquote expandable>{body}</blockquote>"


def _render_thinking(raw_thinking: object, has_partial: bool) -> str:
    thinking = str(raw_thinking or "").strip()
    if not thinking or has_partial:
        return ""
    tail = thinking[-320:]
    if len(thinking) > 320:
        tail = "…" + tail
    return f"💭 <i>Размышляет</i>\n<blockquote expandable><i>{html_escape(tail)}</i></blockquote>"


def _sequence(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []
