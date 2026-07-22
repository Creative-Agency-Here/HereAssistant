from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import native_hooks


def test_install_is_idempotent_and_preserves_unrelated_hooks(tmp_path: Path) -> None:
    settings_path = tmp_path / ".qwen" / "settings.json"
    settings_path.parent.mkdir()
    unrelated = {"hooks": [{"type": "command", "command": "other", "name": "other"}]}
    settings_path.write_text(
        json.dumps({"model": {"name": "test"}, "hooks": {"Stop": [unrelated]}}),
        encoding="utf-8",
    )

    first = native_hooks.install(
        ["qwen_code"], home=tmp_path, backup_root=tmp_path / "backups", python_executable="/python"
    )
    second = native_hooks.install(
        ["qwen_code"], home=tmp_path, backup_root=tmp_path / "backups", python_executable="/python"
    )

    assert first == {"qwen_code": True}
    assert second == {"qwen_code": False}
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["model"] == {"name": "test"}
    assert data["hooks"]["Stop"][0] == unrelated
    assert len(data["hooks"]["Stop"]) == 2
    assert list((tmp_path / "backups" / ".qwen").glob("*.bak"))


def test_uninstall_removes_only_hereassistant_group(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    native_hooks.install(
        ["codex"],
        home=tmp_path,
        backup_root=backup_root,
        python_executable="/python",
    )
    path = tmp_path / ".codex" / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hooks"]["Stop"].insert(0, {"hooks": [{"name": "keep", "command": "keep"}]})
    path.write_text(json.dumps(data), encoding="utf-8")

    changed = native_hooks.uninstall(["codex"], home=tmp_path, backup_root=backup_root)

    assert changed == {"codex": True}
    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["hooks"]["Stop"] == [{"hooks": [{"name": "keep", "command": "keep"}]}]


def test_invalid_settings_are_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        native_hooks.install(["claude_code"], home=tmp_path)

    assert path.read_text(encoding="utf-8") == "{broken"
    assert native_hooks.inspect(["claude_code"], home=tmp_path)[0].state == "invalid"


def test_install_uses_correct_events_and_timeout_units(tmp_path: Path) -> None:
    native_hooks.install(
        home=tmp_path, backup_root=tmp_path / "backups", python_executable="/python"
    )

    expected = {
        "claude_code": ("Stop", 30),
        "codex": ("Stop", 30),
        "qwen_code": ("Stop", 30_000),
        "gemini": ("AfterAgent", 30_000),
    }
    for provider, (event, timeout) in expected.items():
        spec = native_hooks.CLIENTS[provider]
        path = tmp_path.joinpath(*spec.settings_parts)
        data = json.loads(path.read_text(encoding="utf-8"))
        hook = data["hooks"][event][0]["hooks"][0]
        assert hook["name"] == native_hooks.MANAGED_HOOK_NAME
        assert hook["timeout"] == timeout
        assert f"--provider {provider}" in hook["command"]
