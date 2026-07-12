"""Cross-platform subprocess fixture. Never import production modules."""

from __future__ import annotations

import json
import sys

mode = sys.argv[1] if len(sys.argv) > 1 else "echo"
stdin = sys.stdin.read()
if mode == "echo":
    # ASCII JSON не зависит от legacy Windows console code page; json.loads
    # восстановит исходный Unicode на стороне теста.
    print(json.dumps({"args": sys.argv[2:], "stdin": stdin}, ensure_ascii=True))
elif mode == "fail":
    print("fixture failure", file=sys.stderr)
    raise SystemExit(7)
else:
    raise SystemExit(2)
