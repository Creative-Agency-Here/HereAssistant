"""Git-действия режима /rc: preflight, commit, push (этап P5).

CRM/WebApp присылает не shell-команду, а строго типизированный intent. Каждое
действие сначала проходит privacy-гейт ``can_execute_rc_git`` и проверку
доверенного корня, затем выполняет Git локально в том же trusted project root
через существующий ``core.git_projects.run_git``.

Credential получает ТОЛЬКО дочерний процесс git через credential-helper proxy
(``runner/git_credential_proxy.py``) и vault broker (``core/git_vault_client``):
механизм уже настроен в ``run_git``/GitBoundary. В Python-код, результат, лог и
payload команды секрет не попадает никогда — любой git-вывод санитизируется.

Коды ошибок фиксированы (ответ 8 плана RC_REMOTE_CONTROL_PLAN.md):
``AUTH_REQUIRED``, ``REMOTE_DENIED``, ``PREFLIGHT_FAILED``,
``UNKNOWN_RECONCILE_REQUIRED``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional, Union

from core import git_projects, project_config

log = logging.getLogger("bridge.remote_control.git_actions")

# Успешное действие не несёт машинного кода ошибки.
OK = "OK"

# Имя remote — только безопасные символы, без флагов и shell-метасимволов.
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
# Полный SHA коммита — подтверждение того, что коммит действительно создан.
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
# Глобы и shell-метасимволы в путях коммита запрещены.
_PATH_GLOB_CHARS = re.compile(r"[*?\[\]{}!]")
# Явные массовые добавления, которые никогда не принимаются.
_MASS_ADD_TOKENS = frozenset({".", "-A", "--all", "-u", "--update", ":/", ":."})


class RcGitErrorCode(StrEnum):
    """Машинные коды Git-действий /rc (фиксированный набор, без расширений)."""

    AUTH_REQUIRED = "AUTH_REQUIRED"
    REMOTE_DENIED = "REMOTE_DENIED"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    UNKNOWN_RECONCILE_REQUIRED = "UNKNOWN_RECONCILE_REQUIRED"


@dataclass(frozen=True)
class GitActionResult:
    """Итог Git-действия: машинный код + безопасные данные для UI."""

    ok: bool
    code: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """Наружный payload: только машинный контракт, без секретов."""
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "data": self.data,
        }


def _safe(text: str) -> str:
    """Санитизация любого git-вывода: credential не попадает наружу."""
    return git_projects.sanitize_git_output(text)


def _validate_remote_name(remote: str) -> Optional[str]:
    """Имя remote без флагов и метасимволов; иначе None."""
    value = str(remote or "").strip()
    if not value or value.startswith("-") or not _REMOTE_NAME.fullmatch(value):
        return None
    return value


def _validate_commit_paths(paths: Sequence[str]) -> Optional[list[str]]:
    """Только явные относительные пути внутри корня.

    ``git add .``, ``-A``, флаги, глобы и выход за корень (``..``/абсолютный
    путь) отвергаются: коммитить можно лишь перечисленные файлы.
    """
    cleaned: list[str] = []
    for raw in paths:
        value = str(raw).strip()
        if not value or value in _MASS_ADD_TOKENS or value.startswith("-"):
            return None
        if _PATH_GLOB_CHARS.search(value):
            return None
        normalized = Path(value)
        if normalized.is_absolute() or ".." in normalized.parts:
            return None
        cleaned.append(value)
    return cleaned or None


def _gate_denied(policy: project_config.ProjectPolicy) -> Optional[GitActionResult]:
    """Privacy-гейт: приватный проект не исполняет удалённый Git."""
    if project_config.can_execute_rc_git(policy):
        return None
    return GitActionResult(
        ok=False,
        code=RcGitErrorCode.PREFLIGHT_FAILED,
        message="Git-действия /rc запрещены политикой проекта",
    )


def _resolve_root(root: Union[str, Path]) -> Optional[Path]:
    """Доверенный корень: явно настроенный project.yml, без угадывания."""
    return project_config.project_root_for(root)


async def _remote_url(user_id: int, root: Path, remote: str) -> Union[GitActionResult, str]:
    """URL remote для проверки grant; при ошибке — готовый отказ."""
    try:
        url = await git_projects.run_git(
            "remote", "get-url", remote, user_id=user_id, cwd=root
        )
    except git_projects.GitProjectError as error:
        return _from_git_error(error, after_push=False)
    return url.strip()


async def _ensure_repository_grant(
    user_id: int, remote_url: str, *, write: bool
) -> Optional[GitActionResult]:
    """None — доступ разрешён; иначе отказ с машинным кодом."""
    try:
        await git_projects.require_repository_grant(
            user_id, remote_url, write=write, allow_unknown_public=False
        )
    except git_projects.GitAuthRequiredError as error:
        return GitActionResult(False, RcGitErrorCode.AUTH_REQUIRED, _safe(str(error)))
    except git_projects.GitRemoteDeniedError as error:
        return GitActionResult(False, RcGitErrorCode.REMOTE_DENIED, _safe(str(error)))
    except git_projects.GitProjectError as error:
        return GitActionResult(False, RcGitErrorCode.PREFLIGHT_FAILED, _safe(str(error)))
    return None


async def _ensure_remote_allowed(user_id: int, remote_url: str) -> Optional[GitActionResult]:
    """Только разрешённый remote: write-grant на URL. Иначе отказ.

    Это единственная точка решения «разрешён ли remote для push»; обход этой
    проверки означает push в неразрешённый remote.
    """
    try:
        await git_projects.require_repository_grant(
            user_id, remote_url, write=True, allow_unknown_public=False
        )
    except git_projects.GitAuthRequiredError as error:
        return GitActionResult(False, RcGitErrorCode.AUTH_REQUIRED, _safe(str(error)))
    except git_projects.GitRemoteDeniedError as error:
        return GitActionResult(False, RcGitErrorCode.REMOTE_DENIED, _safe(str(error)))
    except git_projects.GitProjectError as error:
        return GitActionResult(False, RcGitErrorCode.REMOTE_DENIED, _safe(str(error)))
    return None


def _from_git_error(
    error: git_projects.GitProjectError, *, after_push: bool
) -> GitActionResult:
    """Картирует нестабильную ошибку Git в фиксированный код /rc.

    ``after_push=True`` означает, что сбой произошёл после фактического начала
    push: результат неизвестен, поэтому код ``UNKNOWN_RECONCILE_REQUIRED``.
    """
    code = error.code
    if code == git_projects.GitErrorCode.AUTH_REQUIRED:
        mapped = RcGitErrorCode.AUTH_REQUIRED
    elif code == git_projects.GitErrorCode.REMOTE_DENIED:
        mapped = RcGitErrorCode.REMOTE_DENIED
    elif after_push:
        mapped = RcGitErrorCode.UNKNOWN_RECONCILE_REQUIRED
    else:
        mapped = RcGitErrorCode.PREFLIGHT_FAILED
    return GitActionResult(False, mapped, _safe(str(error)))


def _parse_porcelain(status: str) -> list[str]:
    """Изменённые пути из ``git status --porcelain`` (read-only разбор)."""
    paths: list[str] = []
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(path)
    return paths


async def _ahead_behind(user_id: int, root: Path) -> dict[str, int]:
    """Ahead/behind относительно upstream; при отсутствии upstream — нули."""
    try:
        output = await git_projects.run_git(
            "rev-list", "--left-right", "--count", "@{upstream}...HEAD",
            user_id=user_id, cwd=root,
        )
        behind, ahead = output.split()[:2]
        return {"ahead": int(ahead), "behind": int(behind)}
    except (git_projects.GitProjectError, ValueError):
        return {"ahead": 0, "behind": 0}


async def _read_remote_ref(user_id: int, root: Path, remote: str) -> Optional[str]:
    """Read-only чтение удалённой ссылки для reconciliation (не push)."""
    try:
        output = await git_projects.run_git(
            "ls-remote", remote, "HEAD", user_id=user_id, cwd=root, timeout=30
        )
    except git_projects.GitProjectError:
        return None
    parts = _safe(output).split()
    return parts[0] if parts else None


async def git_preflight(
    policy: project_config.ProjectPolicy,
    *,
    user_id: int,
    root: Union[str, Path],
    remote: str = "origin",
) -> GitActionResult:
    """Только чтение: корень/grant, ветка, dirty paths, ahead/behind, broker.

    Рабочее дерево не изменяется: используются лишь read-only команды Git
    (remote get-url, symbolic-ref, status, rev-list, ls-remote).
    """
    denied = _gate_denied(policy)
    if denied is not None:
        log.info("rc git_preflight: %s", denied.code)
        return denied
    resolved = _resolve_root(root)
    if resolved is None:
        return GitActionResult(
            False, RcGitErrorCode.PREFLIGHT_FAILED, "Доверенный корень проекта не найден"
        )
    remote_name = _validate_remote_name(remote)
    if remote_name is None:
        return GitActionResult(False, RcGitErrorCode.REMOTE_DENIED, "Некорректный remote")

    url = await _remote_url(user_id, resolved, remote_name)
    if isinstance(url, GitActionResult):
        return url
    grant = await _ensure_repository_grant(user_id, url, write=False)
    if grant is not None:
        log.info("rc git_preflight: %s", grant.code)
        return grant

    data: dict[str, Any] = {"remote": remote_name, "remote_url": _safe(url)}
    try:
        data["branch"] = (
            await git_projects.run_git(
                "symbolic-ref", "--short", "HEAD", user_id=user_id, cwd=resolved
            )
        ).strip()
    except git_projects.GitProjectError:
        # Detached HEAD — не ошибка preflight, ветки просто нет.
        data["branch"] = None
    try:
        status = await git_projects.run_git(
            "status", "--porcelain", user_id=user_id, cwd=resolved
        )
    except git_projects.GitProjectError as error:
        return _from_git_error(error, after_push=False)
    data["dirty_paths"] = _parse_porcelain(status)
    data.update(await _ahead_behind(user_id, resolved))

    # Доступность broker/remote: read-only ls-remote через credential proxy.
    try:
        await git_projects.run_git(
            "ls-remote", remote_name, user_id=user_id, cwd=resolved, timeout=60
        )
        data["broker_available"] = True
    except git_projects.GitAuthRequiredError as error:
        log.info("rc git_preflight: %s", RcGitErrorCode.AUTH_REQUIRED)
        return GitActionResult(False, RcGitErrorCode.AUTH_REQUIRED, _safe(str(error)), data)
    except git_projects.GitRemoteDeniedError as error:
        log.info("rc git_preflight: %s", RcGitErrorCode.REMOTE_DENIED)
        return GitActionResult(False, RcGitErrorCode.REMOTE_DENIED, _safe(str(error)), data)
    except git_projects.GitProjectError:
        data["broker_available"] = False

    log.info("rc git_preflight: %s", OK)
    return GitActionResult(True, OK, "preflight пройден", data)


async def git_commit(
    policy: project_config.ProjectPolicy,
    *,
    user_id: int,
    root: Union[str, Path],
    paths: Sequence[str],
    message: str,
) -> GitActionResult:
    """Коммит строго перечисленных путей. ``git add .`` и массовые добавления запрещены."""
    denied = _gate_denied(policy)
    if denied is not None:
        log.info("rc git_commit: %s", denied.code)
        return denied
    resolved = _resolve_root(root)
    if resolved is None:
        return GitActionResult(
            False, RcGitErrorCode.PREFLIGHT_FAILED, "Доверенный корень проекта не найден"
        )
    clean_paths = _validate_commit_paths(paths)
    if clean_paths is None:
        return GitActionResult(
            False,
            RcGitErrorCode.PREFLIGHT_FAILED,
            "Коммит только явно перечисленных путей; массовые добавления запрещены",
        )
    text = str(message or "").strip()
    if not text:
        return GitActionResult(False, RcGitErrorCode.PREFLIGHT_FAILED, "Пустое сообщение коммита")

    # Разрешение репозитория: коммит локальный, но репозиторий должен быть granted.
    url = await _remote_url(user_id, resolved, "origin")
    if isinstance(url, GitActionResult):
        return url
    grant = await _ensure_repository_grant(user_id, url, write=False)
    if grant is not None:
        log.info("rc git_commit: %s", grant.code)
        return grant

    # Только явные пути: git add -- <paths...>; никаких add . / -A / глобов.
    try:
        await git_projects.run_git("add", "--", *clean_paths, user_id=user_id, cwd=resolved)
        await git_projects.run_git("commit", "-m", text, user_id=user_id, cwd=resolved)
        sha = (
            await git_projects.run_git("rev-parse", "HEAD", user_id=user_id, cwd=resolved)
        ).strip()
    except git_projects.GitProjectError as error:
        log.info("rc git_commit: %s", error.code)
        return _from_git_error(error, after_push=False)

    if not _FULL_SHA.fullmatch(sha):
        return GitActionResult(False, RcGitErrorCode.PREFLIGHT_FAILED, "Коммит не подтверждён")
    log.info("rc git_commit: %s", OK)
    return GitActionResult(True, OK, "коммит создан", {"sha": sha, "paths": clean_paths})


async def git_push(
    policy: project_config.ProjectPolicy,
    *,
    user_id: int,
    root: Union[str, Path],
    remote: str = "origin",
) -> GitActionResult:
    """Push отдельным действием: только разрешённый remote, только fast-forward.

    Сетевой сбой после фактического начала push НЕ повторяется: возвращается
    ``UNKNOWN_RECONCILE_REQUIRED``, далее — только чтение удалённой ссылки.
    """
    denied = _gate_denied(policy)
    if denied is not None:
        log.info("rc git_push: %s", denied.code)
        return denied
    resolved = _resolve_root(root)
    if resolved is None:
        return GitActionResult(
            False, RcGitErrorCode.PREFLIGHT_FAILED, "Доверенный корень проекта не найден"
        )
    remote_name = _validate_remote_name(remote)
    if remote_name is None:
        return GitActionResult(False, RcGitErrorCode.REMOTE_DENIED, "Некорректный remote")

    url = await _remote_url(user_id, resolved, remote_name)
    if isinstance(url, GitActionResult):
        return url
    # Только разрешённый remote: write-grant на URL remote.
    remote_allowed = await _ensure_remote_allowed(user_id, url)
    if remote_allowed is not None:
        log.info("rc git_push: %s", remote_allowed.code)
        return remote_allowed

    # Dry-run preflight ловит non-fast-forward и прочее ДО реального изменения.
    try:
        await git_projects.run_git(
            "push", "--dry-run", remote_name, "HEAD", user_id=user_id, cwd=resolved, timeout=120
        )
    except git_projects.GitProjectError as error:
        log.info("rc git_push: %s", error.code)
        return _from_git_error(error, after_push=False)

    # Реальный push ровно один раз. Повтор при неизвестном исходе запрещён.
    try:
        output = await git_projects.run_git(
            "push", remote_name, "HEAD", user_id=user_id, cwd=resolved, timeout=120
        )
    except git_projects.GitAuthRequiredError as error:
        log.info("rc git_push: %s", RcGitErrorCode.AUTH_REQUIRED)
        return GitActionResult(False, RcGitErrorCode.AUTH_REQUIRED, _safe(str(error)))
    except git_projects.GitRemoteDeniedError as error:
        log.info("rc git_push: %s", RcGitErrorCode.REMOTE_DENIED)
        return GitActionResult(False, RcGitErrorCode.REMOTE_DENIED, _safe(str(error)))
    except git_projects.GitProjectError:
        # Timeout/transport после начала push: исход неизвестен. Повтор
        # заблокирован — только чтение удалённой ссылки для reconciliation.
        remote_ref = await _read_remote_ref(user_id, resolved, remote_name)
        log.info("rc git_push: %s", RcGitErrorCode.UNKNOWN_RECONCILE_REQUIRED)
        return GitActionResult(
            False,
            RcGitErrorCode.UNKNOWN_RECONCILE_REQUIRED,
            "Push мог состояться; повтор заблокирован, сверь удалённую ссылку",
            {"remote": remote_name, "remote_ref": remote_ref},
        )

    log.info("rc git_push: %s", OK)
    return GitActionResult(
        True, OK, "push выполнен", {"remote": remote_name, "summary": _safe(output)[-500:]}
    )
