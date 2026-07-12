"""Реестр проектов и единая проверка доступа к рабочим путям."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import config, db


class ProjectAccessError(PermissionError):
    """Пользователь не имеет доступа к проекту или запрошенному пути."""


class ProjectNotFoundError(LookupError):
    """Проект не найден среди доступных пользователю."""


def _is_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def register_owned_project(user_id: int, name: str, root_path: str | Path) -> sqlite3.Row:
    """Регистрирует private-проект. Вызывается только доверенным кодом/manager UI."""
    root = Path(root_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Корень проекта не является каталогом: {root}")
    now = int(time.time())
    with db.conn() as connection:
        connection.execute(
            """INSERT INTO projects
               (owner_user_id, name, root_path, visibility, enabled, created_at, updated_at)
               VALUES (?, ?, ?, 'private', 1, ?, ?)
               ON CONFLICT(owner_user_id, name) DO UPDATE SET
                   root_path=excluded.root_path, enabled=1, updated_at=excluded.updated_at""",
            (user_id, name, str(root), now, now),
        )
        return connection.execute(
            "SELECT * FROM projects WHERE owner_user_id=? AND name=?", (user_id, name)
        ).fetchone()


def ensure_personal_workspace_projects(user_id: int) -> list[sqlite3.Row]:
    """Регистрирует только прямые реальные каталоги внутри личного workspace."""
    workspace = config.user_workspace(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_root = workspace.resolve(strict=True)
    for item in workspace.iterdir():
        if item.name.startswith(".") or item.is_symlink() or not item.is_dir():
            continue
        resolved = item.resolve(strict=True)
        if _is_within(resolved, workspace_root):
            register_owned_project(user_id, item.name, resolved)
    return list_accessible_projects(user_id)


def ensure_default_project(user_id: int) -> sqlite3.Row:
    root = Path(config.user_default_cwd(user_id))
    return register_owned_project(user_id, "default", root)


def list_accessible_projects(user_id: int) -> list[sqlite3.Row]:
    with db.conn() as connection:
        return list(
            connection.execute(
                """SELECT DISTINCT p.* FROM projects p
                   LEFT JOIN project_members pm
                     ON pm.project_id=p.id AND pm.user_id=?
                   WHERE p.enabled=1
                     AND (p.owner_user_id=? OR (p.visibility='shared' AND pm.user_id IS NOT NULL))
                   ORDER BY (p.owner_user_id=?) DESC, p.name, p.id""",
                (user_id, user_id, user_id),
            )
        )


def get_accessible_project(user_id: int, project_id: int) -> sqlite3.Row | None:
    with db.conn() as connection:
        return connection.execute(
            """SELECT p.* FROM projects p
               LEFT JOIN project_members pm
                 ON pm.project_id=p.id AND pm.user_id=?
               WHERE p.id=? AND p.enabled=1
                 AND (p.owner_user_id=? OR (p.visibility='shared' AND pm.user_id IS NOT NULL))""",
            (user_id, project_id, user_id),
        ).fetchone()


def find_accessible_project(user_id: int, name: str) -> sqlite3.Row | None:
    with db.conn() as connection:
        return connection.execute(
            """SELECT p.* FROM projects p
               LEFT JOIN project_members pm
                 ON pm.project_id=p.id AND pm.user_id=?
               WHERE p.name=? AND p.enabled=1
                 AND (p.owner_user_id=? OR (p.visibility='shared' AND pm.user_id IS NOT NULL))
               ORDER BY (p.owner_user_id=?) DESC, p.id LIMIT 1""",
            (user_id, name, user_id, user_id),
        ).fetchone()


def resolve_authorized_project_path(
    user_id: int, project_id: int, requested_path: str | Path
) -> Path:
    """Разрешает путь только внутри доступного project root, включая symlink-check."""
    project = get_accessible_project(user_id, project_id)
    if project is None:
        raise ProjectNotFoundError(f"Проект {project_id} недоступен")
    root = Path(project["root_path"]).resolve(strict=True)
    requested = Path(requested_path).expanduser()
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ProjectAccessError(f"Путь не существует: {requested_path}") from error
    if not _is_within(resolved, root):
        raise ProjectAccessError("Путь выходит за пределы разрешённого проекта")
    return resolved
