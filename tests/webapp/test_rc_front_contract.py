"""Контракт обращений WebApp к прокси /rc: имена полей, ключ промпта, capabilities.

Экран сессии — единственное место, откуда браузер запускает код на устройстве
владельца, поэтому расхождение имён здесь стоит дорого и тихо: промпт под чужим
ключом доезжает пустой строкой, лишний ключ capabilities молча срезается
валидатором бэкенда и права выглядят выданными, а отправка без ключа
идемпотентности задваивает запуск агента при ретрае.

Проверки статические (по исходнику), потому что vitest/jsdom в этом фронте нет:
задача теста — не дать контракту разъехаться незаметно.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / "webapp" / "front"
REMOTE_CONTROL = FRONT / "composables" / "useRemoteControl.ts"
DEVICE_BADGE = FRONT / "components" / "remote-control" / "RemoteControlDeviceBadge.vue"

# Канонический набор capabilities: RemoteControlCapabilitiesDto бэкенда и тип
# RemoteControlCapabilities схемы. Ключи вне набора глобальный ValidationPipe
# (whitelist: true, forbidNonWhitelisted: false) вырезает без ошибки.
CANONICAL_CAPABILITIES = {"remotePrompt", "stop", "gitCommit", "gitPush", "toolEvents"}


def test_prompt_is_sent_under_canonical_key() -> None:
    source = REMOTE_CONTROL.read_text(encoding="utf-8")
    assert "commandType: 'prompt'" in source
    # Ключ payload читает раннер (chat_remote_control._ingest_prompt_command).
    assert re.search(r"payload:\s*\{\s*prompt:", source)
    # Исторический ключ text означал бы запуск агента с пустым промптом.
    assert not re.search(r"payload:\s*\{\s*text:", source)


def test_prompt_request_carries_idempotency_key() -> None:
    source = REMOTE_CONTROL.read_text(encoding="utf-8")
    assert "headers: { 'Idempotency-Key': attempt.key }" in source


def test_capabilities_interface_is_exactly_canonical() -> None:
    source = REMOTE_CONTROL.read_text(encoding="utf-8")
    block = re.search(r"export interface RcCapabilities \{(.+?)\n\}", source, re.S)
    assert block is not None, "интерфейс RcCapabilities не найден"
    keys = set(re.findall(r"^\s*(\w+)\??:", block.group(1), re.M))
    assert keys == CANONICAL_CAPABILITIES


def test_device_badge_reads_only_canonical_capabilities() -> None:
    source = DEVICE_BADGE.read_text(encoding="utf-8")
    used = set(re.findall(r"caps\.(\w+)", source))
    assert used <= CANONICAL_CAPABILITIES, used - CANONICAL_CAPABILITIES
    # Старые ключи раннера (messages/diffs/commits/git) наружу не уходят вовсе.
    for stale in ("messages", "diffs", "commits", "git"):
        assert not re.search(rf"caps\.{stale}\b", source), stale


def test_prompt_limit_matches_backend_validator() -> None:
    source = REMOTE_CONTROL.read_text(encoding="utf-8")
    assert "export const RC_PROMPT_MAX_CHARS = 8000" in source
