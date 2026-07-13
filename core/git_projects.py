"""Безопасные Git-операции внутри авторизованных пользовательских проектов."""

from __future__ import annotations

import asyncio
import re
import shutil
import sqlite3
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from providers.os_runner import GitBoundary

from . import config, projects

SAFE_NAME = re.compile(r"^[\w-]+$", re.UNICODE)
SSH_URL = re.compile(r"^git@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s]+)$")


class GitErrorCode(StrEnum):
    GIT_FAILED = "GIT_FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    REMOTE_DENIED = "REMOTE_DENIED"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"


class GitProjectError(RuntimeError):
    def __init__(self, message: str, code: GitErrorCode = GitErrorCode.GIT_FAILED):
        super().__init__(message)
        self.code = code

    def payload(self) -> dict[str, str]:
        return {"code": self.code.value, "message": str(self)}


class GitAuthRequiredError(GitProjectError):
    def __init__(self, message: str = "Для Git remote требуется авторизация"):
        super().__init__(message, GitErrorCode.AUTH_REQUIRED)


class GitRemoteDeniedError(GitProjectError):
    def __init__(self, message: str):
        super().__init__(message, GitErrorCode.REMOTE_DENIED)


class GitPushPreflightError(GitProjectError):
    """Останавливает multi-remote push до первого реального изменения remote."""

    def __init__(self, remote: str, cause: GitProjectError):
        super().__init__(
            f"Push preflight для remote '{remote}' не пройден: {cause}",
            GitErrorCode.PREFLIGHT_FAILED,
        )
        self.remote = remote
        self.cause_code = cause.code

    def payload(self) -> dict[str, str]:
        return {
            **super().payload(),
            "remote": self.remote,
            "cause_code": self.cause_code.value,
        }


AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "authentication required",
    "could not read username",
    "terminal prompts disabled",
    "permission denied (publickey)",
    "not logged in",
)
REMOTE_DENIED_MARKERS = (
    "repository not found",
    "does not appear to be a git repository",
    "access denied",
    "not authorized",
    "the requested url returned error: 403",
)
CREDENTIAL_URL_PATTERN = re.compile(r"(https?://)[^\s/@:]+:[^\s@]+@", re.IGNORECASE)
SECRET_ASSIGNMENT_PATTERN = re.compile(r"\b(token|password|authorization)=([^\s&]+)", re.IGNORECASE)


def sanitize_git_output(output: str) -> str:
    value = CREDENTIAL_URL_PATTERN.sub(r"\1[redacted]@", output)
    return SECRET_ASSIGNMENT_PATTERN.sub(r"\1=[redacted]", value)


def classify_git_failure(output: str) -> GitProjectError:
    """Преобразует нестабильный stderr Git в безопасный машинный контракт."""
    normalized = output.casefold()
    if any(marker in normalized for marker in AUTH_FAILURE_MARKERS):
        return GitAuthRequiredError()
    if any(marker in normalized for marker in REMOTE_DENIED_MARKERS):
        return GitRemoteDeniedError("Git remote недоступен или не разрешён")
    sanitized = sanitize_git_output(output)
    return GitProjectError(sanitized[-3000:] or "Git command failed")


def validate_repository_url(url: str, allowed_hosts: tuple[str, ...] | None = None) -> str:
    hosts = set(allowed_hosts or config.GIT_ALLOWED_HOSTS)
    ssh_match = SSH_URL.fullmatch(url)
    if ssh_match:
        if ssh_match.group("host").lower() not in hosts:
            raise GitRemoteDeniedError("Git host не входит в GIT_ALLOWED_HOSTS")
        return url
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise GitRemoteDeniedError("Разрешены только HTTPS или git@host SSH URL")
    if parsed.username or parsed.password:
        raise GitRemoteDeniedError("Credentials в repository URL запрещены")
    if parsed.hostname.lower() not in hosts:
        raise GitRemoteDeniedError("Git host не входит в GIT_ALLOWED_HOSTS")
    return url


def validate_name(value: str, field: str = "name") -> str:
    if not SAFE_NAME.fullmatch(value):
        raise GitProjectError(f"Некорректный {field}: используй буквы, цифры, - и _")
    return value


async def ensure_repository_connection_fresh(user_id: int, url: str) -> None:
    """Прозрачно ротирует истекающий Gitea token внутри owner-scoped vault."""
    from . import git_connections, git_vault_client

    connection_id = git_connections.repository_refresh_target(user_id, url)
    if connection_id is None:
        return
    try:
        expires_at = await git_vault_client.refresh_credential(user_id, connection_id)
        refreshed = git_connections.mark_connection_refreshed(user_id, connection_id, expires_at)
    except (git_connections.GitConnectionError, git_vault_client.GitVaultClientError) as error:
        raise GitAuthRequiredError(
            "Git OAuth истёк, автоматическое обновление не удалось; переподключи Git"
        ) from error
    if not refreshed:
        raise GitAuthRequiredError("Git connection недоступен после автообновления")


async def require_repository_grant(
    user_id: int, url: str, *, write: bool, allow_unknown_public: bool
) -> None:
    if not config.OS_RUNNERS_ENABLED or not url.startswith("https://"):
        return
    from . import git_connections

    await ensure_repository_connection_fresh(user_id, url)
    state = git_connections.repository_grant_state(user_id, url, write=write)
    if state == "allowed" or (state == "unknown" and allow_unknown_public):
        return
    if state == "insufficient":
        raise GitRemoteDeniedError("Для Git write нужен repository grant write/admin")
    raise GitAuthRequiredError("Выбери репозиторий в /git перед этой Git-операцией")


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
        raise classify_git_failure(output or f"git завершился с кодом {process.returncode}")
    return output[-3000:]


async def clone_project(user_id: int, name: str, url: str) -> sqlite3.Row:
    name = validate_name(name)
    url = validate_repository_url(url)
    await require_repository_grant(user_id, url, write=False, allow_unknown_public=True)
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
    directory = Path(root)
    if config.OS_RUNNERS_ENABLED:
        remote_url = await run_git("remote", "get-url", "origin", user_id=user_id, cwd=directory)
        await require_repository_grant(user_id, remote_url, write=False, allow_unknown_public=True)
    return await run_git("pull", "--ff-only", user_id=user_id, cwd=directory, timeout=600)


async def push(user_id: int, root: str | Path) -> str:
    directory = Path(root)
    remotes = (await run_git("remote", user_id=user_id, cwd=directory)).splitlines()
    targets = ["origin"] if "origin" in remotes else []
    if "github" in remotes:
        targets.append("github")
    if not targets:
        raise GitRemoteDeniedError("У репозитория нет origin/github remote")
    if config.OS_RUNNERS_ENABLED:
        for remote in targets:
            remote_url = await run_git("remote", "get-url", remote, user_id=user_id, cwd=directory)
            await require_repository_grant(
                user_id, remote_url, write=True, allow_unknown_public=False
            )
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
            raise GitPushPreflightError(remote, error) from error
    output: list[str] = []
    for remote in targets:
        output.append(
            await run_git("push", remote, "HEAD", user_id=user_id, cwd=directory, timeout=600)
        )
    return "\n".join(part for part in output if part) or "push выполнен"
