"""Безопасная нормализация оболочки запуска для CRM-аналитики."""

from __future__ import annotations

import os
from collections.abc import Mapping


def detect_terminal_app(env: Mapping[str, str] | None = None) -> str | None:
    values = env if env is not None else os.environ
    term_program = values.get("TERM_PROGRAM", "").strip().lower()
    if values.get("VSCODE_INJECTION") or term_program == "vscode":
        return "vscode"
    if values.get("GHOSTTY_RESOURCES_DIR") or "ghostty" in term_program:
        return "ghostty"
    if values.get("ITERM_SESSION_ID") or "iterm" in term_program:
        return "iterm"
    if values.get("WEZTERM_PANE") or "wezterm" in term_program:
        return "wezterm"
    if values.get("WARP_IS_LOCAL_SHELL_SESSION") or "warp" in term_program:
        return "warp"
    if values.get("WT_SESSION"):
        return "windows_terminal"
    if term_program == "apple_terminal":
        return "apple_terminal"
    if "alacritty" in term_program:
        return "alacritty"
    if "kitty" in term_program:
        return "kitty"
    return None


def hereassistant_surface(integration_id: str | None) -> str:
    return "hereassistant_vscode" if integration_id else "hereassistant_cli"
