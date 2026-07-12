"""Run a command with a cross-platform wall-clock timeout and useful CI diagnostics."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
from collections import deque


def _stop_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            text=True,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _annotation(lines: list[str], title: str) -> str:
    detail = " | ".join(line.strip() for line in lines if line.strip())[-4000:]
    detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::error title={title}::{detail}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command is required after --")

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    try:
        output, _ = process.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired as error:
        _stop_tree(process)
        try:
            remainder, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            remainder = ""
        captured = error.output or ""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", errors="replace")
        output = captured + remainder
        tail = list(deque(output.splitlines(), maxlen=30))
        print("\n".join(tail), flush=True)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(_annotation(tail, f"Command timeout after {args.timeout}s"), flush=True)
        return 124

    print(output, end="", flush=True)
    if process.returncode and os.environ.get("GITHUB_ACTIONS") == "true":
        tail = list(deque(output.splitlines(), maxlen=30))
        print(_annotation(tail, f"Command failed with exit code {process.returncode}"), flush=True)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
