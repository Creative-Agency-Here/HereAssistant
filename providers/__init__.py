"""Реестр CLI-провайдеров."""

from core.models import AccountLike

from .base import CLIProvider
from .claude_code import ClaudeCodeProvider
from .codex import CodexProvider
from .gemini import GeminiProvider

REGISTRY = {
    "claude_code": ClaudeCodeProvider,
    "codex": CodexProvider,
    "gemini": GeminiProvider,
}


def make(account: AccountLike, user_id: int | None = None) -> CLIProvider:
    cls = REGISTRY.get(account["provider"])
    if not cls:
        raise ValueError(f"Unknown provider: {account['provider']}")
    return cls(account, user_id=user_id)
