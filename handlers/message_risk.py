"""Уведомление о разрушительных командах агента.

HereAssistant команды не исполняет — их исполняет CLI-агент в своём процессе,
поэтому это монитор, а не блокатор: предупреждение приходит в момент вызова,
остановить его шлюз не может. Называть это защитой нельзя.

Текст самой команды сюда не попадает: `RiskAlertDict` содержит только уровень,
сработавшие правила и цели.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from providers.models import RiskAlertDict

_LEVEL_TITLES: dict[str, str] = {
    "CONFIRM": "⚠️ Агент запускает потенциально разрушительную команду",
    "CATASTROPHIC": "🛑 Агент запускает команду с катастрофическим радиусом",
}

_RULE_TITLES: dict[str, str] = {
    "protected_root": "системный корень",
    "protected_recursive": "защищённый системный каталог",
    "home_root": "домашний каталог целиком",
    "sensitive_dotdir": "каталог с ключами и учётными данными",
    "protected_dotdir_exact": "каталог конфигурации",
    "dev_write": "запись в устройство",
    "protected_glob": "маска в защищённом каталоге",
    "glob_target": "маска в цели",
    "unresolved_substitution": "неразрешимая подстановка в пути",
    "outside_cwd": "путь вне рабочего каталога",
    "piped_input": "цели приходят из конвейера",
    "no_target": "цель не определяется",
    "empty_wrapper": "команда спрятана за обёрткой",
}


def alerts_from_meta(meta: Mapping[str, Any] | None) -> list[RiskAlertDict]:
    """Достаёт вердикты из meta провайдера, не доверяя её форме."""
    if not meta:
        return []
    raw = meta.get("risk_alerts")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("level")]


def alert_key(alert: Mapping[str, Any]) -> tuple[str, str, str]:
    """Ключ дедупликации: один и тот же вердикт не повторяем за turn."""
    rules = ",".join(str(rule) for rule in alert.get("rules") or [])
    targets = ",".join(str(target) for target in alert.get("targets") or [])
    return str(alert.get("level")), rules, targets


def format_alert(alert: Mapping[str, Any]) -> str:
    """Человекочитаемое предупреждение без текста команды."""
    level = str(alert.get("level", "CONFIRM"))
    title = _LEVEL_TITLES.get(level, _LEVEL_TITLES["CONFIRM"])
    lines = [title]
    tool = str(alert.get("tool") or "").strip()
    if tool:
        lines.append(f"Инструмент: {tool}")
    reasons = [
        _RULE_TITLES.get(str(rule), str(rule))
        for rule in alert.get("rules") or []
        if str(rule).strip()
    ]
    if reasons:
        lines.append("Причина: " + ", ".join(reasons))
    targets = [str(target) for target in alert.get("targets") or [] if str(target).strip()]
    if targets:
        lines.append("Цель: " + ", ".join(targets[:3]))
    lines.append("Команду выполняет сам CLI-агент — шлюз её не останавливает.")
    return "\n".join(lines)


def new_alerts(
    alerts: Iterable[Mapping[str, Any]],
    seen: set[tuple[str, str, str]],
) -> list[Mapping[str, Any]]:
    """Оставляет только вердикты, о которых ещё не сообщали."""
    fresh: list[Mapping[str, Any]] = []
    for alert in alerts:
        key = alert_key(alert)
        if key in seen:
            continue
        seen.add(key)
        fresh.append(alert)
    return fresh
