"""Оценка риска shell-вызовов агента — общая для всех парсеров.

Классификация делается здесь, где полный ввод инструмента ещё доступен, а
наружу отдаётся только вердикт: сама команда за пределы парсера не уходит.
"""

from __future__ import annotations

from typing import Any

from core import command_risk
from providers.models import RiskAlertDict

# Инструменты, чей ввод — shell-команда. Имена различаются между CLI.
SHELL_TOOLS = frozenset(
    {
        "Bash",
        "PowerShell",
        "Shell",
        "run_shell_command",
        "run_terminal_cmd",
        "shell",
    }
)


def record_risk(
    name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str | None,
    alerts: list[RiskAlertDict],
) -> None:
    """Добавляет вердикт в `alerts`, если вызов дотягивает до CONFIRM.

    Безопасные команды молчат: иначе предупреждение перестают читать.
    """
    if name not in SHELL_TOOLS:
        return
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return
    assessment = command_risk.assess(command, cwd=cwd)
    if assessment.level.value < command_risk.RiskLevel.CONFIRM.value:
        return
    alert = RiskAlertDict(
        tool=name,
        level=assessment.level.name,
        rules=sorted({finding.rule for finding in assessment.findings}),
        targets=sorted({finding.target for finding in assessment.findings if finding.target}),
    )
    if alert not in alerts:
        alerts.append(alert)
