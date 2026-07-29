"""Привязка «сессия ↔ публикация /rc» на стороне WebApp.

Правило живёт в JS (``webapp/front/utils/rcBinding.mjs``) и проверяется своим
node-тестом. Здесь он поднимается в общий прогон ``pytest tests``: иначе
единственная защита от возврата сопоставления «по устройству» осталась бы за
границей обязательных проверок и молча перестала бы запускаться.

Дополнительно фиксируется структура: composable экрана сессии обязан спрашивать
цель у общего правила, а не искать публикацию по deviceId самостоятельно.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / "webapp" / "front"
NODE_TEST = FRONT / "tests" / "rc-binding.test.mjs"
BINDING_UTIL = FRONT / "utils" / "rcBinding.mjs"
SESSION_COMPOSABLE = FRONT / "composables" / "useSessionRemoteControl.ts"


def test_binding_rule_node_test_passes() -> None:
    """Прогон node-теста правила привязки в составе обязательных проверок."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node недоступен: правило привязки проверяется через npm run test:rc")
    # Команда фиксированная, пользовательского ввода в аргументах нет.
    completed = subprocess.run(
        [node, "--test", str(NODE_TEST)],
        cwd=FRONT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_session_composable_delegates_to_shared_binding_rule() -> None:
    """Экран сессии не имеет собственного поиска публикации по устройству.

    Своя копия правила — это способ незаметно вернуть сопоставление по машине:
    промпт из карточки старой сессии проекта A уехал бы в текущую публикацию
    проекта B того же компьютера.
    """
    source = SESSION_COMPOSABLE.read_text(encoding="utf-8")
    assert "resolveRcSessionPublication" in source
    # Локальный перебор публикаций в composable запрещён — правило одно.
    assert "publications.value.find" not in source


def test_remote_control_has_no_device_or_latest_fallback() -> None:
    """У composable управления нет другого способа выбрать цель, кроме id публикации.

    Фолбэки «публикация этого устройства» и «самая свежая публикация владельца» —
    это и есть отправка промпта в чужой проект, поэтому их не должно быть даже как
    неиспользуемой ветки.
    """
    source = (FRONT / "composables" / "useRemoteControl.ts").read_text(encoding="utf-8")
    assert "item.deviceId === " not in source
    assert "publications.value[0]" not in source


def test_binding_rule_requires_conversation_match() -> None:
    """Сверка диалога обязательна и не заменяется совпадением устройства."""
    source = BINDING_UTIL.read_text(encoding="utf-8")
    assert "publication.conversationId !== conversationId" in source
    assert "if (!conversationId) return null" in source
