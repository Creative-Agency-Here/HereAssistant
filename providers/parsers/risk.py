"""Оценка риска shell-вызовов агента — общая для всех парсеров.

Классификация делается здесь, где полный ввод инструмента ещё доступен, а в
вердикт попадают только уровень, правила и цели — без текста команды.

Оговорка, чтобы не создавать ложного впечатления: шлюз в принципе видит команду
и показывает её начало в описании шага (`ToolStep.desc`, до 80 символов) — так
было и до монитора. Инвариант этого модуля уже: **вердикт** не добавляет к этому
ни одного нового символа команды, в том числе её хвоста и аргументов, которые в
короткое описание не поместились.
"""

from __future__ import annotations

from typing import Any

from core import command_risk
from providers.models import RiskAlertDict

# Инструменты, чей ввод — shell-команда. Имена различаются между CLI.
SHELL_TOOLS = command_risk.SHELL_TOOL_NAMES


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
