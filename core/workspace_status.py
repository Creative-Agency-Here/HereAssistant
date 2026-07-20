"""Safe, read-only workspace/CRM/Git status used by CLI, Telegram and Web App."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config, db, git_connections, project_config, projects

FINISHED_TASK_STATUSES = frozenset({"done", "completed", "closed", "cancelled", "canceled"})


def _run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_snapshot(root: str | Path | None) -> dict[str, Any]:
    """Return bounded Git metadata without remotes, credentials or file names."""
    if not root:
        return {"available": False}
    try:
        path = Path(root).expanduser().resolve(strict=True)
    except OSError:
        return {"available": False}
    output = _run_git(path, "status", "--porcelain=v2", "--branch")
    if output is None:
        return {"available": False}

    branch = "detached"
    ahead = 0
    behind = 0
    dirty = 0
    for line in output.splitlines():
        if line.startswith("# branch.head "):
            branch = line.removeprefix("# branch.head ").strip()
        elif line.startswith("# branch.ab "):
            for token in line.removeprefix("# branch.ab ").split():
                if token.startswith("+"):
                    ahead = int(token[1:] or 0)
                elif token.startswith("-"):
                    behind = int(token[1:] or 0)
        elif line and not line.startswith("#"):
            dirty += 1

    if dirty:
        state = "changes"
    elif ahead and behind:
        state = "diverged"
    elif ahead:
        state = "push_needed"
    elif behind:
        state = "pull_needed"
    else:
        state = "synced"
    head = _run_git(path, "rev-parse", "HEAD")
    return {
        "available": True,
        "branch": branch,
        "head": head[:12] if head else None,
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "state": state,
    }


def deployment_snapshot(root: str | Path | None, head: str | None) -> dict[str, Any]:
    """Read an optional hook-written marker; never guess deployment from Git state."""
    if not root:
        return {"state": "unknown", "targets": []}
    marker = Path(root) / ".hereassistant" / "deploy-state.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return {"state": "unknown", "targets": []}
    if not isinstance(payload, dict):
        return {"state": "unknown", "targets": []}

    targets = payload.get("targets")
    clean_targets: list[dict[str, Any]] = []
    if isinstance(targets, dict):
        for name, value in list(targets.items())[:20]:
            if not isinstance(value, dict):
                continue
            clean_targets.append(
                {
                    "name": " ".join(str(name).split())[:80],
                    "commit": str(value.get("commit") or "")[:12] or None,
                    "status": str(value.get("status") or "unknown")[:40],
                }
            )
    marker_commit = str(payload.get("commit") or "")[:12] or None
    commits = [item["commit"] for item in clean_targets if item["commit"]]
    if marker_commit:
        commits.append(marker_commit)
    if not commits or not head:
        state = "unknown"
    elif all(head.startswith(commit) or commit.startswith(head) for commit in commits):
        state = "deployed"
    elif any(head.startswith(commit) or commit.startswith(head) for commit in commits):
        state = "partial"
    else:
        state = "pending"
    return {
        "state": state,
        "targets": clean_targets,
        "deployedAt": str(payload.get("deployedAt") or "")[:40] or None,
    }


def task_summary(cwd: str | Path | None) -> dict[str, Any]:
    policy = project_config.policy_for(cwd)
    project_id = policy.crm_project_id
    if not project_id:
        return {"linked": False, "open": 0, "titles": []}
    with db.conn() as connection:
        rows = connection.execute(
            """SELECT title,status FROM tasks WHERE crm_project_id=?
               ORDER BY updated_at DESC LIMIT 100""",
            (project_id,),
        ).fetchall()
    active = [row for row in rows if str(row["status"]).casefold() not in FINISHED_TASK_STATUSES]
    return {
        "linked": True,
        "crmProjectId": project_id,
        "crmTaskId": policy.crm_task_id,
        "open": len(active),
        "titles": [" ".join(str(row["title"]).split())[:120] for row in active[:5]],
    }


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if amount < 1024 or unit == "ТБ":
            return f"{amount:.0f} {unit}" if unit in ("Б", "КБ", "МБ") else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} ТБ"


def workspace_overview(user_id: int, cwd: str | Path | None = None) -> dict[str, Any]:
    workspace = config.user_workspace(user_id)
    try:
        usage = shutil.disk_usage(workspace if workspace.exists() else config.BASE_DIR)
        disk = {
            "freeBytes": usage.free,
            "totalBytes": usage.total,
            "freeLabel": _format_bytes(usage.free),
        }
    except OSError:
        disk = {"freeBytes": None, "totalBytes": None, "freeLabel": "неизвестно"}

    known_projects = projects.list_accessible_projects(user_id)
    existing = []
    repositories = 0
    for row in known_projects[:100]:
        try:
            root = Path(row["root_path"]).resolve(strict=True)
        except OSError:
            continue
        if root.is_dir():
            existing.append(row)
            # Worktrees use a .git file, normal repositories a .git directory.
            # Counting the marker keeps /start instant even with many projects.
            if (root / ".git").exists():
                repositories += 1

    connections = git_connections.list_connections(user_id)
    grants = git_connections.list_repository_grants(user_id)
    current_git = git_snapshot(cwd)
    return {
        "projectsOnDisk": len(existing),
        "repositoriesOnDisk": repositories,
        "disk": disk,
        "git": {
            "connections": sum(row["status"] == "active" for row in connections),
            "attention": sum(row["status"] in ("expired", "error") for row in connections),
            "repositories": sum(bool(row["enabled"]) for row in grants),
            "current": current_git,
        },
        "tasks": task_summary(cwd),
        "deployment": deployment_snapshot(cwd, current_git.get("head")),
    }


def installation_identity() -> dict[str, str]:
    host = config.HEREASSISTANT_CONTOUR_HOST or config.HERECRM_SYNC_ORIGIN or socket.gethostname()
    label = config.HEREASSISTANT_CONTOUR_NAME or host
    return {
        "id": host.casefold(),
        "label": label,
        "kind": config.HEREASSISTANT_CONTOUR_KIND,
        "originHost": host,
    }


def parse_activity_at(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None
