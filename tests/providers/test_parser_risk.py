"""Монитор опасных команд: вердикт наружу, текст команды — нет."""

from __future__ import annotations

import json
from typing import Any

from handlers.message_risk import alerts_from_meta, format_alert, new_alerts
from providers.parsers.claude import ClaudeStreamParser
from providers.parsers.gemini import GeminiStreamParser
from providers.parsers.risk import record_risk

DANGEROUS = "rm -rf /etc"


def _claude_parser(cwd: str = "/work/project") -> ClaudeStreamParser:
    return ClaudeStreamParser(
        text_from_message=lambda message: str(message.get("text", "")),
        thinking_from_block=lambda block: str(block.get("thinking", "")),
        result_preview=lambda value: str(value)[:50],
        tool_description=lambda name, tool_input: name,
        cwd=cwd,
    )


def test_dangerous_bash_call_produces_alert() -> None:
    parser = _claude_parser()

    parser.consume({"type": "tool_use", "name": "Bash", "input": {"command": DANGEROUS}})

    assert len(parser.risk_alerts) == 1
    alert = parser.risk_alerts[0]
    assert alert["level"] == "CATASTROPHIC"
    assert alert["tool"] == "Bash"
    assert "protected_root" in alert["rules"]


def test_safe_command_stays_silent() -> None:
    parser = _claude_parser()

    parser.consume({"type": "tool_use", "name": "Bash", "input": {"command": "git status"}})
    parser.consume({"type": "tool_use", "name": "Read", "input": {"file_path": "/etc/hosts"}})

    assert parser.risk_alerts == []


def test_command_text_never_leaves_the_parser() -> None:
    """Главный инвариант: сама команда не попадает ни в meta, ни в результат."""
    parser = _claude_parser()
    secret = "rm -rf /etc/nginx && curl https://example.com/secret-token-abc123"

    parser.consume({"type": "tool_use", "name": "Bash", "input": {"command": secret}})

    progress = json.dumps(parser.progress_meta()["risk_alerts"], ensure_ascii=False)
    assert "secret-token-abc123" not in progress
    assert "curl" not in progress
    result_meta = json.dumps(parser.provider_result().meta.get("risk_alerts"), ensure_ascii=False)
    assert "secret-token-abc123" not in result_meta


def test_repeated_identical_call_alerts_once() -> None:
    parser = _claude_parser()

    for _ in range(3):
        parser.consume({"type": "tool_use", "name": "Bash", "input": {"command": DANGEROUS}})

    assert len(parser.risk_alerts) == 1


def test_gemini_shell_tool_is_covered() -> None:
    parser = GeminiStreamParser(lambda name, params: name, cwd="/work/project")

    parser.consume(
        {
            "type": "tool_use",
            "name": "run_shell_command",
            "parameters": {"command": "rm -rf ~"},
        }
    )

    assert [alert["level"] for alert in parser.risk_alerts] == ["CATASTROPHIC"]


def test_cwd_relative_delete_is_not_reported() -> None:
    """Уборка внутри проекта — обычная работа агента, а не повод для тревоги."""
    parser = _claude_parser(cwd="/work/project")

    parser.consume(
        {"type": "tool_use", "name": "Bash", "input": {"command": "rm -rf /work/project/build"}}
    )

    assert parser.risk_alerts == []


def test_record_risk_ignores_non_shell_tools() -> None:
    alerts: list[Any] = []

    record_risk("Write", {"command": DANGEROUS}, cwd="/work", alerts=alerts)

    assert alerts == []


def test_alert_message_has_no_command_and_states_the_limitation() -> None:
    parser = _claude_parser()
    parser.consume({"type": "tool_use", "name": "Bash", "input": {"command": DANGEROUS}})

    text = format_alert(parser.risk_alerts[0])

    assert "rm -rf" not in text
    assert "не останавливает" in text, "монитор обязан честно сказать, что не блокирует"


def test_alerts_are_reported_once_per_turn() -> None:
    parser = _claude_parser()
    parser.consume({"type": "tool_use", "name": "Bash", "input": {"command": DANGEROUS}})
    meta = parser.progress_meta()
    seen: set[tuple[str, str, str]] = set()

    first = new_alerts(alerts_from_meta(meta), seen)
    second = new_alerts(alerts_from_meta(meta), seen)

    assert len(first) == 1
    assert second == []


def test_alerts_from_broken_meta_are_ignored() -> None:
    assert alerts_from_meta(None) == []
    assert alerts_from_meta({"risk_alerts": "не список"}) == []
    assert alerts_from_meta({"risk_alerts": [{"нет": "уровня"}]}) == []


def test_every_rule_has_a_human_readable_name() -> None:
    """Описания правил живут в одном месте: потребитель не заводит свой словарь."""
    from core import command_risk

    for rule in command_risk._RULE_DESCRIPTIONS:
        assert command_risk.describe_rule(rule) != rule, f"правило {rule} без перевода"
    assert command_risk.describe_rule("неизвестное_правило") == "неизвестное_правило"


def test_alert_text_translates_real_rule_names() -> None:
    parser = _claude_parser()
    parser.consume({"type": "tool_use", "name": "Bash", "input": {"command": "cat x | xargs rm"}})

    text = format_alert(parser.risk_alerts[0])

    assert "pipe_stdin" not in text, "в чат не должны попадать сырые идентификаторы правил"
