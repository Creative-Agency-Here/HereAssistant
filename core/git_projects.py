"""Безопасные Git-операции внутри авторизованных пользовательских проектов."""

from __future__ import annotations

import asyncio
import re
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from providers.os_runner import GitBoundary

from . import config, projects

SAFE_NAME = re.compile(r"^[\w-]+$", re.UNICODE)
SSH_URL = re.compile(r"^git@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s]+)$")


class GitProjectError(RuntimeError):
    pass


class GitPushPreflightError(GitProjectError):
    """Останавливает multi-remote push до первого реального изменения remote."""


def validate_repository_url(url: str, allowed_hosts: tuple[str, ...] | None = None) -> str:
    hosts = set(allowed_hosts or config.GIT_ALLOWED_HOSTS)
    ssh_match = SSH_URL.fullmatch(url)
    if ssh_match:
        if ssh_match.group("host").lower() not in hosts:
            raise GitProjectError("Git host не входит в GIT_ALLOWED_HOSTS")
        return url
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise GitProjectError("Разрешены только HTTPS или git@host SSH URL")
    if parsed.username or parsed.password:
        raise GitProjectError("Credentials в repository URL запрещены")
    if parsed.hostname.lower() not in hosts:
        raise GitProjectError("Git host не входит в GIT_ALLOWED_HOSTS")
    return url


def validate_name(value: str, field: str = "name") -> str:
    if not SAFE_NAME.fullmatch(value):
        raise GitProjectError(f"Некорректный {field}: используй буквы, цифры, - и _")
    return value


async def run_git(
    *args: str, user_id: int | None = None, cwd: Path | None = None, timeout: int = 300
) -> str:
    if config.OS_RUNNERS_ENABLED and user_id is None:
        raise GitProjectError("OS runner Git требует user_id")
    directory = str(cwd or config.user_workspace(user_id or 0))
    prepared = GitBoundary(user_id or 0).prepare(["git", *args], directory)
    prepared.env["GIT_CEILING_DIRECTORIES"] = str(Path(directory).parent)
    process = await asyncio.create_subprocess_exec(
        *prepared.argv,
        cwd=prepared.cwd,
        env=prepared.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise GitProjectError(f"git timeout after {timeout}s")
    output = (stdout + stderr).decode(errors="replace").strip()
    if process.returncode:
        raise GitProjectError(output[-3000:] or f"git завершился с кодом {process.returncode}")
    return output[-3000:]


async def clone_project(user_id: int, name: str, url: str) -> sqlite3.Row:
    name = validate_name(name)
    url = validate_repository_url(url)
    workspace = config.user_workspace(user_id).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    destination = workspace / name
    if destination.exists():
        raise GitProjectError(f"Каталог проекта уже существует: {name}")
    try:
        await run_git(
            "clone", "--", url, str(destination), user_id=user_id, cwd=workspace, timeout=600
        )
        return projects.register_owned_project(user_id, name, destination)
    except asyncio.CancelledError:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except (GitProjectError, OSError, sqlite3.Error, ValueError):
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise


async def create_worktree(user_id: int, project_id: int, branch: str) -> sqlite3.Row:
    branch = validate_name(branch, "branch")
    project = projects.get_accessible_project(user_id, project_id)
    if project is None or project["owner_user_id"] != user_id:
        raise GitProjectError("Worktree можно создавать только из собственного проекта")
    root = Path(project["root_path"]).resolve(strict=True)
    if not (root / ".git").exists():
        raise GitProjectError("Выбранный проект не является Git-репозиторием")
    destination = config.user_workspace(user_id) / ".worktrees" / project["name"] / branch
    if destination.exists():
        raise GitProjectError(f"Worktree уже существует: {branch}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    await run_git("worktree", "add", "-b", branch, str(destination), user_id=user_id, cwd=root)
    return projects.register_owned_project(user_id, f"{project['name']}--{branch}", destination)


async def status(user_id: int, root: str | Path) -> str:
    return await run_git("status", "--short", "--branch", user_id=user_id, cwd=Path(root))


async def pull(user_id: int, root: str | Path) -> str:
    return await run_git("pull", "--ff-only", user_id=user_id, cwd=Path(root), timeout=600)


async def push(user_id: int, root: str | Path) -> str:
    directory = Path(root)
    remotes = (await run_git("remote", user_id=user_id, cwd=directory)).splitlines()
    targets = ["origin"] if "origin" in remotes else []
    if "github" in remotes:
        targets.append("github")
    if not targets:
        raise GitProjectError("У репозитория нет origin/github remote")
    for remote in targets:
        try:
            await run_git(
                "push",
                "--dry-run",
                remote,
                "HEAD",
                user_id=user_id,
                cwd=directory,
                timeout=600,
            )
        except GitProjectError as error:
            raise GitPushPreflightError(
                f"Push preflight для remote '{remote}' не пройден: {error}"
            ) from error
    output: list[str] = []
    for remote in targets:
        output.append(
            await run_git("push", remote, "HEAD", user_id=user_id, cwd=directory, timeout=600)
        )
    return "\n".join(part for part in output if part) or "push выполнен"
