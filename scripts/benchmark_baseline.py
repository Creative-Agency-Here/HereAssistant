#!/usr/bin/env python3
"""Small reproducible import startup/RSS baseline for future TS comparison."""

from __future__ import annotations

import json
import subprocess
import sys

TARGETS = ("providers", "handlers.message_progress", "webapp.api.server")


def measure(module: str) -> dict[str, float | str]:
    code = (
        "import importlib,json,resource,sys,time;"
        "start=time.perf_counter();importlib.import_module(sys.argv[1]);"
        "elapsed=(time.perf_counter()-start)*1000;"
        "rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;"
        "rss=rss/(1024*1024) if sys.platform=='darwin' else rss/1024;"
        "print(json.dumps({'module':sys.argv[1],'startup_ms':round(elapsed,2),"
        "'max_rss_mib':round(rss,2)}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, module], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def main() -> int:
    print(json.dumps([measure(module) for module in TARGETS], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
