#!/usr/bin/env python3
"""Связывает native Claude memory текущего cwd с общей памятью HereAssistant."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from core import agent_memory, db, project_config, projects


def encoded_cwd(cwd: str | Path) -> str:
    return str(Path(cwd).resolve()).replace(":", "-").replace("\\", "-").replace("/", "-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--claude-home", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db.init()
    project = projects.get_accessible_project(args.user_id, args.project_id)
    if project is None:
        raise SystemExit("Проект недоступен указанному пользователю")
    policy = project_config.policy_for(project["root_path"])
    if not project_config.can_use_agent_memory(policy):
        raise SystemExit("В project.yml не включён agent.memory.enabled")

    claude_home = args.claude_home.expanduser().resolve(strict=True)
    shared = agent_memory.shared_directory(project["root_path"])
    native = claude_home / "projects" / encoded_cwd(project["root_path"]) / "memory"
    if native.is_symlink():
        if native.resolve() == shared.resolve():
            print(f"already-linked={native}")
            return 0
        raise SystemExit("Native memory уже ссылается на другой каталог")
    if native.exists() and any(native.iterdir()):
        raise SystemExit(
            "Native memory не пустая: сначала импортируй её через import_claude_memory.py"
        )
    print(f"native={native}")
    print(f"shared={shared}")
    if args.dry_run:
        return 0

    shared.mkdir(parents=True, exist_ok=True, mode=0o700)
    shared.chmod(0o700)
    native.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if native.exists():
        native.rmdir()
    os.symlink(shared, native, target_is_directory=True)
    print("linked=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
