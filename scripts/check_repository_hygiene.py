#!/usr/bin/env python3
"""Проверяет tracked/untracked candidate files на runtime artifacts и secrets."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {
    ".env",
    "bridge.sqlite3",
    "credentials.json",
    ".credentials.json",
    "auth.json",
    "oauth_creds.json",
}
FORBIDDEN_PARTS = {".runtime", "node_modules", ".nuxt", ".output", "__pycache__"}
SECRET_PATTERNS = (
    re.compile("hvs" + r"\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{25,}\b"),
    re.compile("AIza" + r"[0-9A-Za-z_-]{20,}"),
    re.compile(r"https?://[^\s/:]+:[^\s@]+@"),
    re.compile("BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY"),
)


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def violations(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        try:
            relative = path.relative_to(ROOT)
        except ValueError:
            relative = path
        if (
            path.name in FORBIDDEN_NAMES
            or FORBIDDEN_PARTS.intersection(relative.parts)
            or relative.as_posix() == "webapp/front/dist"
        ):
            failures.append(f"runtime/auth artifact: {relative}")
            continue
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            failures.append(f"potential secret: {relative}")
    return failures


def main() -> int:
    failures = violations(candidate_files())
    if failures:
        print("\n".join(failures))
        return 1
    print("repository hygiene: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
