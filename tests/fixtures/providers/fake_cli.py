"""Cross-platform subprocess fixture. Never import production modules."""

from __future__ import annotations

import json
import sys

mode = sys.argv[1] if len(sys.argv) > 1 else "echo"
stdin = sys.stdin.read()
if mode == "echo":
    print(json.dumps({"args": sys.argv[2:], "stdin": stdin}, ensure_ascii=False))
elif mode == "fail":
    print("fixture failure", file=sys.stderr)
    raise SystemExit(7)
else:
    raise SystemExit(2)
