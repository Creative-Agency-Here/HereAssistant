"""Реестр CLI-провайдеров."""

import sqlite3
from .base import CLIProvider
from .claude_code import ClaudeCodeProvider
from .codex import CodexProvider
from .gemini import GeminiProvider

REGISTRY = {
    "claude_code": ClaudeCodeProvider,
    "codex": CodexProvider,
    "gemini": GeminiProvider,
}


def make(account: sqlite3.Row) -> CLIProvider:
    cls = REGISTRY.get(account["provider"])
    if not cls:
        raise ValueError(f"Unknown provider: {account['provider']}")
    return cls(account)
