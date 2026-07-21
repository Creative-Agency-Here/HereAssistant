"""Детерминированный secret-scan для импортируемой памяти без вывода значений."""

from __future__ import annotations

import re

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("here_vault", re.compile(r"\bhvs\.[A-Za-z0-9_-]{10,}")),
    ("telegram_bot", re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{25,}\b")),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("credential_url", re.compile(r"https?://[^\s/:]+:[^\s@]+@")),
    ("private_key", re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")),
    (
        "provider_key",
        re.compile(r"\b(?:sk-(?:ant|proj|sp)-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{24,})\b"),
    ),
    (
        "assigned_secret",
        re.compile(
            r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password)\s*[:=]\s*[^\s`]{12,}",
            re.IGNORECASE,
        ),
    ),
)


def detected_secret_types(text: str) -> tuple[str, ...]:
    """Возвращает только названия классов, никогда не найденные значения."""
    return tuple(name for name, pattern in SECRET_PATTERNS if pattern.search(text))
