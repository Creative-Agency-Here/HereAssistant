"""Terminal UI primitives менеджера."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence

if os.name == "nt":
    os.system("")

G = "\033[92m"
R = "\033[91m"
Y = "\033[93m"
C = "\033[96m"
M = "\033[95m"
W = "\033[97m"
B = "\033[1m"
D = "\033[2m"
X = "\033[0m"
BG_G = "\033[42m"
BG_R = "\033[41m"
BG_Y = "\033[43m"
BG_B = "\033[44m"
BLACK = "\033[30m"

MenuItem = tuple[str, str, str, str]


def getch() -> str:
    try:
        # `msvcrt.getwch()` не учитывает redirected/captured stdin и навсегда
        # блокируется в CI. Сначала проверяем, что stdin действительно terminal.
        descriptor = sys.stdin.fileno()
        if not sys.stdin.isatty():
            return input().strip()[:1]
        if os.name == "nt":
            import msvcrt

            return msvcrt.getwch()
        import termios
        import tty

        previous = termios.tcgetattr(descriptor)
        try:
            tty.setraw(descriptor)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
    except (ImportError, OSError, ValueError, AttributeError):
        return input().strip()[:1]


def press_any_key(prompt: str = "Нажми любую клавишу для меню...") -> None:
    print(f"\n{D}{prompt}{X}", end="", flush=True)
    getch()
    print()


def line(char: str = "─", width: int = 64, color: str = D) -> str:
    return f"{color}{char * width}{X}"


def box_top(width: int = 64) -> str:
    return f"{C}╭{'─' * (width - 2)}╮{X}"


def box_bot(width: int = 64) -> str:
    return f"{C}╰{'─' * (width - 2)}╯{X}"


def box_mid(text: str, width: int = 64, color: str = "") -> str:
    padding = max(0, width - 4 - len(strip_ansi(text)))
    return f"{C}│{X} {color}{text}{X}{' ' * padding} {C}│{X}"


def strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)


def badge(text: str, foreground: str = BLACK, background: str = BG_G) -> str:
    return f"{background}{foreground} {text} {X}"


def logo() -> None:
    art = [
        "██╗  ██╗███████╗██████╗ ███████╗",
        "██║  ██║██╔════╝██╔══██╗██╔════╝",
        "███████║█████╗  ██████╔╝█████╗  ",
        "██╔══██║██╔══╝  ██╔══██╗██╔══╝  ",
        "██║  ██║███████╗██║  ██║███████╗",
        "╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝",
    ]
    print()
    for entry in art:
        print(f"  {B}{M}{entry}{X}")
    print(f"  {D}·  A S S I S T A N T  ·  мульти-CLI Telegram-мост{X}")


def render_menu(items: Sequence[MenuItem]) -> str:
    print()
    for key, icon, name, description in items:
        hint = f"   {D}— {description}{X}" if description else ""
        print(f"  {B}[{key}]{X}  {icon}  {name}{hint}")
    print(f"\n{D}нажми клавишу (без Enter){X} › ", end="", flush=True)
    return getch()
