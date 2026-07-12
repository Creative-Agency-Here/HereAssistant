from webapp.api.models import (
    git_connection_to_dto,
    parse_git_connection_start,
    parse_git_repository_grant,
    parse_task_create,
    parse_task_patch,
)


def test_task_create_requires_mapping_project_and_title() -> None:
    assert parse_task_create([]) is None
    assert parse_task_create({}) is None
    assert parse_task_create({"crm_project_id": "crm-1"}) is None


def test_task_create_normalizes_and_bounds_external_fields() -> None:
    payload = parse_task_create(
        {
            "crm_project_id": " crm-1 ",
            "title": "x" * 600,
            "status": "y" * 50,
            "meta": {"source": "test"},
        }
    )

    assert payload is not None
    assert payload["crm_project_id"] == "crm-1"
    assert len(payload["title"]) == 500
    assert len(payload["status"]) == 32
    assert payload["meta"] == {"source": "test"}


def test_task_patch_allows_only_known_mutable_fields() -> None:
    assert parse_task_patch("invalid") is None
    assert parse_task_patch({"crm_project_id": "forbidden"}) is None

    payload = parse_task_patch(
        {"status": " done ", "title": " New title ", "meta": {}, "unknown": "ignored"}
    )
    assert payload == {"status": "done", "title": "New title", "meta": {}}


def test_git_connection_start_accepts_only_known_provider_and_host() -> None:
    assert parse_git_connection_start([]) is None
    assert parse_git_connection_start({"provider": "unknown", "host": "github.com"}) is None
    assert parse_git_connection_start({"provider": "github", "host": "https://github.com"}) is None
    assert parse_git_connection_start({"provider": "gitea", "host": "user@host"}) is None
    assert parse_git_connection_start(
        {"provider": " GITHUB ", "host": " GitHub.COM ", "user_id": 999}
    ) == {"provider": "github", "host": "github.com"}


def test_git_repository_grant_uses_only_connection_and_external_id() -> None:
    assert parse_git_repository_grant({}) is None
    assert (
        parse_git_repository_grant({"connection_id": "invalid", "external_repository_id": 1})
        is None
    )
    assert (
        parse_git_repository_grant({"connection_id": 0, "external_repository_id": "repo"}) is None
    )
    assert parse_git_repository_grant(
        {
            "connection_id": "42",
            "external_repository_id": " repo-1 ",
            "clone_url": "ignored",
            "user_id": 999,
        }
    ) == {"connection_id": 42, "external_repository_id": "repo-1"}


def test_git_connection_response_never_serializes_vault_reference() -> None:
    payload = git_connection_to_dto(
        {
            "id": 1,
            "provider": "gitea",
            "host": "git.example.com",
            "external_user_id": "42",
            "external_login": "alice",
            "avatar_url": None,
            "vault_ref": "vault://must-not-leak",
            "scopes_json": '["read:user","write:repository"]',
            "status": "active",
            "expires_at": None,
            "updated_at": 100,
            "last_used_at": None,
        }
    )

    assert payload["scopes"] == ["read:user", "write:repository"]
    assert "vault_ref" not in payload
    assert "must-not-leak" not in str(payload)
