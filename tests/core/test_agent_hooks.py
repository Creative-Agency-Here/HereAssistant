import json
from pathlib import Path

from core.agent_hooks import EXPECTED_EVENTS, readiness


def write_hooks(path: Path, events: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"hooks": {event: [{"hooks": [{"command": "test"}]}] for event in events}}),
        encoding="utf-8",
    )


def test_codex_requires_complete_versioned_hooks(tmp_path: Path) -> None:
    write_hooks(tmp_path / ".codex" / "hooks.json", set(EXPECTED_EVENTS))

    result = readiness(tmp_path, "codex")

    assert result.state == "ready"
    assert set(result.events) == EXPECTED_EVENTS


def test_claude_distinguishes_template_from_installed_hooks(tmp_path: Path) -> None:
    write_hooks(tmp_path / ".claude" / "hooks.template.json", set(EXPECTED_EVENTS))

    assert readiness(tmp_path, "claude_code").state == "template-only"

    write_hooks(tmp_path / ".claude" / "settings.local.json", set(EXPECTED_EVENTS))

    assert readiness(tmp_path, "claude_code").state == "ready"


def test_partial_or_invalid_hook_config_is_missing(tmp_path: Path) -> None:
    write_hooks(tmp_path / ".codex" / "hooks.json", {"Stop"})
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "hooks.template.json").write_text("not-json", encoding="utf-8")

    assert readiness(tmp_path, "codex").state == "missing"
    assert readiness(tmp_path, "claude_code").state == "missing"
    assert readiness(tmp_path, "qwen_code").state == "native"
