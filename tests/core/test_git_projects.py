import subprocess
from pathlib import Path

import pytest

from core import config, db, git_projects, projects


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
    with pytest.raises(git_projects.GitProjectError):
        git_projects.validate_repository_url(url, ("github.com",))


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
            raise git_projects.GitProjectError("authentication required")
        return "ok"

    monkeypatch.setattr(git_projects, "run_git", fake_run_git)

    with pytest.raises(
        git_projects.GitPushPreflightError,
        match="remote 'github'.*authentication required",
    ):
        await git_projects.push(100, tmp_path)

    assert calls == [
        ("remote",),
        ("push", "--dry-run", "origin", "HEAD"),
        ("push", "--dry-run", "github", "HEAD"),
    ]
