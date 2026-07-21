"""Статическая конфигурация manager CLI."""

from __future__ import annotations

import os
from pathlib import Path

from manage_accounts import ProviderSpec

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bridge.sqlite3"
RUNTIME_DIR = BASE_DIR / ".runtime"
CLI_HOMES_DIR = RUNTIME_DIR / "cli_homes"
ENV_PATH = BASE_DIR / ".env"

PROVIDERS: dict[str, ProviderSpec] = {
    "1": {
        "key": "claude_code",
        "title": "Claude Code",
        "subtitle": "Anthropic",
        "bin": "claude",
        "npm_pkg": "@anthropic-ai/claude-code",
        "env_var": "CLAUDE_CONFIG_DIR",
        "default_model": "claude-opus-4-7",
        "login_hint": "В TUI пройди /login и выбери свой Max/Pro аккаунт. Затем /exit.",
    },
    "2": {
        "key": "codex",
        "title": "Codex CLI",
        "subtitle": "OpenAI",
        "bin": "codex",
        "npm_pkg": "@openai/codex",
        "env_var": "CODEX_HOME",
        "default_model": "gpt-5",
        "login_hint": "Откроется браузер для OAuth — авторизуйся в OpenAI-аккаунте.",
    },
    "3": {
        "key": "gemini",
        "title": "Gemini CLI",
        "subtitle": "Google",
        "bin": "gemini",
        "npm_pkg": "@google/gemini-cli",
        "env_var": "USERPROFILE" if os.name == "nt" else "HOME",
        "default_model": "gemini-2.5-pro",
        "login_hint": "В TUI пройди /auth и авторизуйся в Google-аккаунте. Затем /exit.",
    },
    "4": {
        "key": "qwen_code",
        "title": "Qwen Code",
        "subtitle": "Alibaba Cloud Model Studio",
        "bin": "qwen",
        "npm_pkg": "@qwen-code/qwen-code@latest",
        "env_var": "QWEN_HOME",
        "default_model": "qwen3.7-plus",
        "login_hint": (
            "В TUI выполни /auth → Alibaba ModelStudio → Token Plan или Coding Plan, "
            "введи plan-specific ключ и затем /exit."
        ),
    },
}
