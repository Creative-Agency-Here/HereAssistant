import subprocess
from pathlib import Path

import pytest

from core import config, db, git_connections, git_projects, git_vault_client, projects


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / ".runtime"
    for name, value in {
        "RUNTIME_DIR": runtime,
        "DOWNLOADS_DIR": runtime / "downloads",
        "LOGS_DIR": runtime / "logs",
        "BACKUPS_DIR": runtime / "backups",
        "STATE_DIR": runtime / "state",
        "CLI_HOMES_DIR": runtime / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setattr(config, "ADMIN_ID", None)
    db.init()
    return tmp_path


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/project.git",
        "git@github.com:example/project.git",
    ],
)
def test_repository_url_accepts_only_allowed_hosts_without_credentials(url: str) -> None:
    assert git_projects.validate_repository_url(url, ("github.com",)) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/project.git",
        "https://user:" + "secret@github.com/example/project.git",
        "https://evil.example/project.git",
        "git@evil.example:project.git",
        "file:///tmp/project",
    ],
)
def test_repository_url_rejects_unsafe_sources(url: str) -> None:
    with pytest.raises(git_projects.GitRemoteDeniedError) as captured:
        git_projects.validate_repository_url(url, ("github.com",))
    assert captured.value.code == git_projects.GitErrorCode.REMOTE_DENIED


@pytest.mark.parametrize(
    ("output", "error_type", "code"),
    [
        (
            "fatal: Authentication failed for remote",
            git_projects.GitAuthRequiredError,
            git_projects.GitErrorCode.AUTH_REQUIRED,
        ),
        (
            "remote: Repository not found",
            git_projects.GitRemoteDeniedError,
            git_projects.GitErrorCode.REMOTE_DENIED,
        ),
        (
            "fatal: unexpected transport error",
            git_projects.GitProjectError,
            git_projects.GitErrorCode.GIT_FAILED,
        ),
    ],
)
def test_git_failure_has_stable_machine_code(
    output: str,
    error_type: type[git_projects.GitProjectError],
    code: git_projects.GitErrorCode,
) -> None:
    error = git_projects.classify_git_failure(output)

    assert isinstance(error, error_type)
    assert error.code == code
    assert error.payload()["code"] == code.value


def test_git_failure_payload_redacts_credentials() -> None:
    credential_url = "https://alice:" + "private-value" + "@example.com/repo.git"
    error = git_projects.classify_git_failure(
        f"fatal: transport failed at {credential_url} token=private-value"
    )

    assert "private-value" not in str(error)
    assert "private-value" not in error.payload()["message"]
    assert "[redacted]" in str(error)


@pytest.mark.asyncio
async def test_hardened_write_requires_explicit_repository_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "OS_RUNNERS_ENABLED", True)
    monkeypatch.setattr(git_connections, "repository_refresh_target", lambda *_args: None)
    monkeypatch.setattr(
        git_connections,
        "repository_grant_state",
        lambda _user_id, _url, *, write: "disabled" if write else "unknown",
    )

    with pytest.raises(git_projects.GitAuthRequiredError, match="Выбери репозиторий"):
        await git_projects.require_repository_grant(
            100,
            "https://git.example.com/alice/project.git",
            write=True,
            allow_unknown_public=False,
        )
    await git_projects.require_repository_grant(
        100,
        "https://git.example.com/public/project.git",
        write=False,
        allow_unknown_public=True,
    )


@pytest.mark.asyncio
async def test_expired_repository_connection_is_refreshed_transparently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "OS_RUNNERS_ENABLED", True)
    refresh_target_calls = 0

    def refresh_target(_user_id: int, _url: str) -> int | None:
        nonlocal refresh_target_calls
        refresh_target_calls += 1
        return 7 if refresh_target_calls == 1 else None

    monkeypatch.setattr(git_connections, "repository_refresh_target", refresh_target)
    monkeypatch.setattr(
        git_connections, "repository_grant_state", lambda *_args, **_kwargs: "allowed"
    )
    refreshed: list[tuple[int, int]] = []

    async def refresh(user_id: int, connection_id: int) -> int:
        refreshed.append((user_id, connection_id))
        return 2_000_000_000

    monkeypatch.setattr(git_vault_client, "refresh_credential", refresh)
    monkeypatch.setattr(git_connections, "mark_connection_refreshed", lambda *_args: True)

    await git_projects.require_repository_grant(
        100,
        "https://git.example.com/alice/project.git",
        write=True,
        allow_unknown_public=False,
    )

    assert refreshed == [(100, 7)]


@pytest.mark.asyncio
async def test_failed_automatic_refresh_requires_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "OS_RUNNERS_ENABLED", True)
    monkeypatch.setattr(git_connections, "repository_refresh_target", lambda *_args: 7)

    async def fail(_user_id: int, _connection_id: int) -> int:
        raise git_vault_client.GitVaultClientError("refresh failed")

    monkeypatch.setattr(git_vault_client, "refresh_credential", fail)

    with pytest.raises(git_projects.GitAuthRequiredError, match="переподключи Git"):
        await git_projects.require_repository_grant(
            100,
            "https://git.example.com/alice/project.git",
            write=True,
            allow_unknown_public=False,
        )


@pytest.mark.asyncio
async def test_worktree_is_created_and_registered_per_user(project_db: Path) -> None:
    root = config.user_workspace(100) / "source"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    (root / "README.md").write_text("test", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    source = projects.register_owned_project(100, "source", root)

    worktree = await git_projects.create_worktree(100, source["id"], "feature-1")

    worktree_path = Path(worktree["root_path"])
    assert worktree["name"] == "source--feature-1"
    assert worktree_path.is_dir()
    assert (worktree_path / ".git").exists()
    assert projects.get_accessible_project(100, worktree["id"]) is not None
    assert projects.get_accessible_project(200, worktree["id"]) is None
    assert "feature-1" in await git_projects.status(100, worktree_path)


@pytest.mark.asyncio
async def test_clone_failure_removes_partial_destination(
    project_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*_args: str, **_kwargs: object) -> str:
        destination = config.user_workspace(100) / "broken"
        destination.mkdir(parents=True)
        raise git_projects.GitProjectError("clone failed")

    monkeypatch.setattr(git_projects, "run_git", fail)

    with pytest.raises(git_projects.GitProjectError, match="clone failed"):
        await git_projects.clone_project(100, "broken", "https://github.com/example/project.git")
    assert not (config.user_workspace(100) / "broken").exists()


@pytest.mark.asyncio
async def test_push_preflights_all_remotes_before_real_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_git(*args: str, **_kwargs: object) -> str:
        calls.append(args)
        if args == ("remote",):
            return "origin\ngithub"
        return f"{' '.join(args)} ok"

    monkeypatch.setattr(git_projects, "run_git", fake_run_git)

    result = await git_projects.push(100, tmp_path)

    assert calls == [
        ("remote",),
        ("push", "--dry-run", "origin", "HEAD"),
        ("push", "--dry-run", "github", "HEAD"),
        ("push", "origin", "HEAD"),
        ("push", "github", "HEAD"),
    ]
    assert "push origin HEAD ok" in result
    assert "--dry-run" not in result


@pytest.mark.asyncio
async def test_push_stops_before_real_push_when_any_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_git(*args: str, **_kwargs: object) -> str:
        calls.append(args)
        if args == ("remote",):
            return "origin\ngithub"
        if args == ("push", "--dry-run", "github", "HEAD"):
            raise git_projects.GitAuthRequiredError()
        return "ok"

    monkeypatch.setattr(git_projects, "run_git", fake_run_git)

    with pytest.raises(
        git_projects.GitPushPreflightError,
        match="remote 'github'.*требуется авторизация",
    ) as captured:
        await git_projects.push(100, tmp_path)

    assert captured.value.payload() == {
        "code": "PREFLIGHT_FAILED",
        "message": str(captured.value),
        "remote": "github",
        "cause_code": "AUTH_REQUIRED",
    }

    assert calls == [
        ("remote",),
        ("push", "--dry-run", "origin", "HEAD"),
        ("push", "--dry-run", "github", "HEAD"),
    ]
