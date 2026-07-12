"""Pure parsers текстового вывода Codex CLI."""

from __future__ import annotations


def extract_session_id(stdout: str, stderr: str, current: str | None = None) -> str | None:
    session_id = current
    for line in (stderr + "\n" + stdout).splitlines():
        if "session" not in line.lower() or "id" not in line.lower():
            continue
        for token in line.replace(":", " ").replace(",", " ").split():
            if len(token) >= 16 and "-" in token:
                session_id = token.strip().strip('"').strip("'")
                break
    return session_id
