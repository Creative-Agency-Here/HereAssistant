#!/usr/bin/env python3
"""PreToolUse-хук Claude Code и Qwen Code: отклоняет команды с катастрофическим радиусом.

Единственное место, где HereAssistant может реально остановить действие агента,
а не сообщить о нём постфактум. Всё остальное — наблюдение.

Один и тот же скрипт годится обоим CLI: контракт ответа совпадает —
`hookSpecificOutput.permissionDecision = deny`.

Дизайн продиктован проверенным поведением Claude Code (см. SECURITY.md):
при таймауте хука команда ВЫПОЛНЯЕТСЯ. Поэтому здесь нет ни сети, ни обращений
к БД, ни ожидания ответа человека — только локальная лексическая проверка,
занимающая миллисекунды. Ждать подтверждения в Telegram внутри хука нельзя:
пока человек читает сообщение, таймаут откроет проход.

Блокируется только уровень CATASTROPHIC (`rm -rf /`, `$HOME`, `~/.ssh`, запись в
устройства). Уровень CONFIRM намеренно пропускается: ложная блокировка обычной
работы дороже, чем предупреждение, которое и так приходит в чат.

Отключается переменной окружения HEREASSISTANT_RISK_HOOK=0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.command_risk import (  # noqa: E402
    SHELL_TOOL_NAMES,
    RiskLevel,
    assess,
    describe_rule,
)

# Ограничение на размер входа: хук читает stdin от чужого процесса.
_MAX_INPUT_BYTES = 1_000_000
# Список имён — общий с монитором (core.command_risk), чтобы предупреждение и
# блокировка срабатывали на одних и тех же инструментах.


def _allow() -> None:
    """Разрешить вызов: пустой вывод и нулевой код."""
    sys.exit(0)


def _deny(reason: str) -> None:
    """Отклонить вызов. Причина видна снаружи как содержимое tool_result."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    sys.exit(0)


def main() -> None:
    if os.environ.get("HEREASSISTANT_RISK_HOOK", "1") in ("0", "false", "no", "off"):
        _allow()

    raw = sys.stdin.read(_MAX_INPUT_BYTES)
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Непонятный ввод — не наше дело блокировать работу агента.
        _allow()
    if not isinstance(payload, dict):
        _allow()

    if payload.get("tool_name") not in SHELL_TOOL_NAMES:
        _allow()

    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        _allow()

    cwd = payload.get("cwd")
    assessment = assess(command, cwd=cwd if isinstance(cwd, str) else None)
    if assessment.level is not RiskLevel.CATASTROPHIC:
        _allow()

    reasons = ", ".join(sorted({describe_rule(finding.rule) for finding in assessment.findings}))
    targets = ", ".join(
        sorted({finding.target for finding in assessment.findings if finding.target})[:3]
    )
    reason = f"HereAssistant заблокировал команду: {reasons}"
    if targets:
        reason += f" ({targets})"
    reason += ". Если это действительно нужно — выполните вручную сами."
    _deny(reason)


if __name__ == "__main__":
    main()
