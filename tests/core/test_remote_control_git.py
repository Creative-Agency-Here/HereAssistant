"""Git-действия /rc (P5): гейты, read-only preflight, явные пути, коды ошибок.

Git-команды подменяются заглушкой ``GitStub`` — реальные репозитории не
создаются. Проверяется фиксированный набор кодов и отсутствие утечки секретов.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import git_projects
from core.project_config import PRIVATE, ProjectPolicy
from core.remote_control import git_actions

FULL_SHA = "a" * 40
REMOTE_URL = "https://github.com/example/project.git"


class GitStub:
    """Заглушка ``git_projects.run_git``: пишет вызовы, отдаёт ответы."""

    def __init__(self, responses: dict[tuple, object] | None = None) -> None:
        self.calls: list[tuple] = []
        self.responses = responses or {}

    async def __call__(self, *args: str, **_kwargs: object) -> str:
        self.calls.append(args)
        result = self.responses.get(args, "")
        if isinstance(result, BaseException):
            raise result
        return result

    def commands(self) -> list[str]:
        return [call[0] for call in self.calls]


def allow_policy() -> ProjectPolicy:
    """Политика, проходящая гейт can_execute_rc_git (активный CRM-канал)."""
    return ProjectPolicy(
        mode="crm",
        crm_project_id="project-1",
        sync_enabled=True,
        rc_enabled=True,
    )


async def allow_grant(*_args: object, **_kwargs: object) -> None:
    """Заглушка require_repository_grant: доступ разрешён."""
    return None


@pytest.fixture
def trusted_root(tmp_path: Path) -> Path:
    """Доверенный корень: явный .hereassistant/project.yml."""
    (tmp_path / ".hereassistant").mkdir()
    (tmp_path / ".hereassistant" / "project.yml").write_text("mode: crm\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def git_stub(monkeypatch: pytest.MonkeyPatch) -> GitStub:
    stub = GitStub()
    monkeypatch.setattr(git_projects, "run_git", stub)
    return stub


# --- Тест 1: запрет при выключенном гейте (fail closed, без запуска Git) ---


@pytest.mark.asyncio
async def test_gate_off_denies_all_actions_without_running_git(
    git_stub: GitStub, trusted_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_projects, "require_repository_grant", allow_grant)

    preflight = await git_actions.git_preflight(PRIVATE, user_id=1, root=trusted_root)
    commit = await git_actions.git_commit(
        PRIVATE, user_id=1, root=trusted_root, paths=["a.py"], message="m"
    )
    push = await git_actions.git_push(PRIVATE, user_id=1, root=trusted_root)

    for result in (preflight, commit, push):
        assert result.ok is False
        assert result.code == git_actions.RcGitErrorCode.PREFLIGHT_FAILED
    # Гейт срабатывает до любого обращения к Git.
    assert git_stub.calls == []


# --- Тест 2: preflight не меняет рабочее дерево (только read-only команды) ---


@pytest.mark.asyncio
async def test_preflight_is_read_only(
    git_stub: GitStub, trusted_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_projects, "require_repository_grant", allow_grant)
    git_stub.responses.update(
        {
            ("remote", "get-url", "origin"): REMOTE_URL,
            ("symbolic-ref", "--short", "HEAD"): "main",
            ("status", "--porcelain"): " M a.py\n?? b.py\nR  old.py -> new.py",
            ("rev-list", "--left-right", "--count", "@{upstream}...HEAD"): "1\t2",
            ("ls-remote", "origin"): "deadbeef\tHEAD",
        }
    )

    result = await git_actions.git_preflight(allow_policy(), user_id=1, root=trusted_root)

    assert result.ok is True
    mutating = {
        "add", "commit", "checkout", "clean", "reset", "push", "stash",
        "merge", "rebase", "rm", "mv", "apply", "cherry-pick", "restore",
    }
    assert mutating.isdisjoint(set(git_stub.commands()))
    # Разбор состояния рабочего дерева без его изменения.
    assert result.data["branch"] == "main"
    assert result.data["dirty_paths"] == ["a.py", "b.py", "new.py"]
    assert result.data["ahead"] == 2
    assert result.data["behind"] == 1
    assert result.data["broker_available"] is True


# --- Тест 3: commit берёт только перечисленные пути ---


@pytest.mark.asyncio
async def test_commit_stages_only_listed_paths(
    git_stub: GitStub, trusted_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_projects, "require_repository_grant", allow_grant)
    git_stub.responses.update(
        {
            ("remote", "get-url", "origin"): REMOTE_URL,
            ("rev-parse", "HEAD"): FULL_SHA,
        }
    )

    result = await git_actions.git_commit(
        allow_policy(),
        user_id=1,
        root=trusted_root,
        paths=["a.py", "dir/b.py"],
        message="fix: точечные правки",
    )

    assert result.ok is True
    assert result.data["sha"] == FULL_SHA
    # Только явные пути, через разделитель --, без массовых добавлений.
    assert ("add", "--", "a.py", "dir/b.py") in git_stub.calls
    add_calls = [call for call in git_stub.calls if call[0] == "add"]
    assert add_calls == [("add", "--", "a.py", "dir/b.py")]
    # Сообщение передаётся отдельным argv, без shell-интерполяции.
    assert ("commit", "-m", "fix: точечные правки") in git_stub.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paths",
    [
        ["."],
        ["-A"],
        ["--all"],
        ["../secret.py"],
        ["/abs/path.py"],
        ["*.py"],
        [""],
        [],
    ],
)
async def test_commit_rejects_mass_or_unsafe_paths(
    git_stub: GitStub, trusted_root: Path, monkeypatch: pytest.MonkeyPatch, paths: list[str]
) -> None:
    monkeypatch.setattr(git_projects, "require_repository_grant", allow_grant)

    result = await git_actions.git_commit(
        allow_policy(), user_id=1, root=trusted_root, paths=paths, message="m"
    )

    assert result.ok is False
    assert result.code == git_actions.RcGitErrorCode.PREFLIGHT_FAILED
    # До git add дело не доходит.
    assert all(call[0] != "add" for call in git_stub.calls)


# --- Тест 4: push отклоняет неразрешённый remote с REMOTE_DENIED ---


@pytest.mark.asyncio
async def test_push_rejects_unallowed_remote(
    git_stub: GitStub, trusted_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_stub.responses[("remote", "get-url", "evil")] = "https://evil.example/x/y.git"

    async def deny_write(*_args: object, write: bool = False, **_kwargs: object) -> None:
        if write:
            raise git_projects.GitRemoteDeniedError("Git remote недоступен или не разрешён")

    monkeypatch.setattr(git_projects, "require_repository_grant", deny_write)

    result = await git_actions.git_push(allow_policy(), user_id=1, root=trusted_root, remote="evil")

    assert result.ok is False
    assert result.code == git_actions.RcGitErrorCode.REMOTE_DENIED
    # Push (включая dry-run) не запускается для неразрешённого remote.
    assert all(call[0] != "push" for call in git_stub.calls)


# --- Тест 5: таймаут после push → UNKNOWN_RECONCILE_REQUIRED, без повтора ---


@pytest.mark.asyncio
async def test_push_timeout_after_send_is_unknown_without_retry(
    git_stub: GitStub, trusted_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_projects, "require_repository_grant", allow_grant)
    git_stub.responses.update(
        {
            ("remote", "get-url", "origin"): REMOTE_URL,
            ("push", "--dry-run", "origin", "HEAD"): "",
            ("push", "origin", "HEAD"): git_projects.GitProjectError("git timeout after 120s"),
            ("ls-remote", "origin", "HEAD"): "deadbeef\tHEAD",
        }
    )

    result = await git_actions.git_push(allow_policy(), user_id=1, root=trusted_root)

    assert result.ok is False
    assert result.code == git_actions.RcGitErrorCode.UNKNOWN_RECONCILE_REQUIRED
    # Реальный push выполнен ровно один раз — повтора после таймаута нет.
    assert git_stub.calls.count(("push", "origin", "HEAD")) == 1
    # Reconciliation — только чтение удалённой ссылки.
    assert result.data["remote_ref"] == "deadbeef"


# --- Тест 6: секрет не появляется ни в результате, ни в логах ---


@pytest.mark.asyncio
async def test_secret_never_leaks_into_result_or_logs(
    git_stub: GitStub,
    trusted_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(git_projects, "require_repository_grant", allow_grant)
    secret = "supersecrettoken123"
    # URL с встроенными credentials собирается из частей: цельная такая
    # строка в файле репозитория срабатывает на детекторе секретов
    # (scripts/check_repository_hygiene.py), хотя значение здесь фиктивное.
    leaked_url = "https://" + f"alice:{secret}" + "@github.com/example/project.git"
    git_stub.responses.update(
        {
            ("remote", "get-url", "origin"): leaked_url,
            ("push", "--dry-run", "origin", "HEAD"): "",
            ("push", "origin", "HEAD"): f"To {leaked_url}\n   abc..def  main -> main",
        }
    )

    with caplog.at_level("DEBUG"):
        result = await git_actions.git_push(allow_policy(), user_id=1, root=trusted_root)

    assert result.ok is True
    serialized = json.dumps(result.payload(), ensure_ascii=False)
    assert secret not in serialized
    assert "[redacted]" in serialized
    assert secret not in caplog.text
