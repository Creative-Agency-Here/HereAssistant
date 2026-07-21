"""Read-only readiness проверки нативных lifecycle hooks в текущем проекте."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EXPECTED_EVENTS = frozenset(
    {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
)


@dataclass(frozen=True, slots=True)
class HookReadiness:
    state: str
    events: tuple[str, ...] = ()


def _events(path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        hooks = payload.get("hooks")
        if not isinstance(hooks, dict):
            return ()
        return tuple(
            sorted(
                event
                for event, groups in hooks.items()
                if event in EXPECTED_EVENTS and isinstance(groups, list) and groups
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return ()


def readiness(project_root: str | Path, provider: str) -> HookReadiness:
    root = Path(project_root).resolve()
    if provider == "codex":
        events = _events(root / ".codex" / "hooks.json")
        return HookReadiness("ready" if set(events) == EXPECTED_EVENTS else "missing", events)
    if provider == "claude_code":
        local_events = _events(root / ".claude" / "settings.local.json")
        if set(local_events) == EXPECTED_EVENTS:
            return HookReadiness("ready", local_events)
        template_events = _events(root / ".claude" / "hooks.template.json")
        if set(template_events) == EXPECTED_EVENTS:
            return HookReadiness("template-only", template_events)
        return HookReadiness("missing", local_events or template_events)
    if provider == "qwen_code":
        events = _events(root / ".qwen" / "settings.json")
        return HookReadiness("ready" if set(events) == EXPECTED_EVENTS else "missing", events)
    return HookReadiness("native")
