"""Установщик hooks не переписывает чужой файл настроек незнакомой формы."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import native_hooks


def test_foreign_hooks_shape_is_not_overwritten(tmp_path: Path) -> None:
    """Раньше чужие hooks молча заменялись нашей группой."""
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    # Форма, которую мы не понимаем: hooks задан списком, а не объектом.
    foreign = {"hooks": ["чужая настройка"], "permissions": {"allow": ["Bash(ls:*)"]}}
    settings.write_text(json.dumps(foreign), encoding="utf-8")

    changed = native_hooks.install(["claude_code"], home=home, backup_root=tmp_path / "backups")

    assert changed["claude_code"] is False, "установка обязана отказаться, а не переписать"
    assert json.loads(settings.read_text(encoding="utf-8")) == foreign, "чужой файл не тронут"


def test_foreign_event_shape_is_not_overwritten(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    foreign = {"hooks": {"Stop": {"неожиданно": "объект вместо списка"}}}
    settings.write_text(json.dumps(foreign), encoding="utf-8")

    changed = native_hooks.install(["claude_code"], home=home, backup_root=tmp_path / "backups")

    assert changed["claude_code"] is False
    assert json.loads(settings.read_text(encoding="utf-8")) == foreign


def test_uninstall_also_refuses_foreign_shape(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    foreign = {"hooks": ["чужая настройка"]}
    settings.write_text(json.dumps(foreign), encoding="utf-8")

    changed = native_hooks.uninstall(["claude_code"], home=home, backup_root=tmp_path / "backups")

    assert changed["claude_code"] is False
    assert json.loads(settings.read_text(encoding="utf-8")) == foreign


def test_normal_shape_still_installs(tmp_path: Path) -> None:
    """Штатная форма по-прежнему обрабатывается и сохраняет чужие группы."""
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "чужой"}]}]}}),
        encoding="utf-8",
    )

    changed = native_hooks.install(["claude_code"], home=home, backup_root=tmp_path / "backups")

    assert changed["claude_code"] is True
    payload = json.loads(settings.read_text(encoding="utf-8"))
    groups = payload["hooks"]["Stop"]
    assert any("чужой" in json.dumps(group, ensure_ascii=False) for group in groups), (
        "чужая группа обязана сохраниться"
    )


@pytest.mark.parametrize("broken", ['{"hooks": 5}', '{"hooks": "строка"}'])
def test_scalar_hooks_are_refused(tmp_path: Path, broken: str) -> None:
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(broken, encoding="utf-8")

    changed = native_hooks.install(["claude_code"], home=home, backup_root=tmp_path / "backups")

    assert changed["claude_code"] is False
