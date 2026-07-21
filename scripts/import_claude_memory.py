#!/usr/bin/env python3
"""Импортирует Markdown-memory Claude в общую owner/project-scoped память."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from core import agent_memory, db, project_config, projects
from core.secret_scan import detected_secret_types

MAX_SOURCE_FILE_BYTES = 1_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--copy-to-shared",
        action="store_true",
        help="Скопировать безопасные Markdown-файлы в .hereassistant/memory проекта",
    )
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
    source_dir = args.source_dir.expanduser().resolve(strict=True)
    if not source_dir.is_dir():
        raise SystemExit("source-dir не является каталогом")

    target_dir = agent_memory.shared_directory(project["root_path"])
    if args.copy_to_shared and not args.dry_run:
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_dir.chmod(0o700)

    stats = {
        "found": 0,
        "imported": 0,
        "unchanged": 0,
        "copied": 0,
        "secret_skipped": 0,
        "large": 0,
        "unsafe": 0,
    }
    for path in sorted(source_dir.glob("*.md")):
        stats["found"] += 1
        try:
            if path.is_symlink() or not path.is_file():
                stats["unsafe"] += 1
                print(f"SKIP unsafe-link: {path.name}")
                continue
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                stats["large"] += 1
                print(f"SKIP large: {path.name}")
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            stats["unsafe"] += 1
            print(f"SKIP unreadable: {path.name}")
            continue
        secret_types = detected_secret_types(content)
        if secret_types:
            stats["secret_skipped"] += 1
            print(f"SKIP secret: {path.name} ({','.join(secret_types)})")
            continue
        if args.dry_run:
            stats["imported"] += 1
            continue
        source_id = path.name
        if args.copy_to_shared:
            target = target_dir / path.name
            if target.exists() and target.read_text(encoding="utf-8") != content:
                suffix = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
                target = target_dir / f"{path.stem}.imported-{suffix}.md"
            if not target.exists():
                target.write_text(content, encoding="utf-8")
                target.chmod(0o600)
                stats["copied"] += 1
            source_id = target.name
        changed = agent_memory.upsert(
            user_id=args.user_id,
            project_id=args.project_id,
            source="shared" if args.copy_to_shared else "claude",
            source_id=source_id,
            title=path.stem.replace("-", " "),
            content=content,
        )
        stats["imported" if changed else "unchanged"] += 1
    print(" ".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
