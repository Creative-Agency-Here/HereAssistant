import pytest

from webapp.api.gitea_oauth import GiteaOAuthClientError, _repository


def test_repository_metadata_uses_gitea_permissions() -> None:
    repository = _repository(
        {
            "id": 77,
            "owner": {"login": "alice"},
            "name": "project",
            "clone_url": "https://git.example.com/alice/project.git",
            "default_branch": "main",
            "permissions": {"pull": True, "push": True, "admin": False},
        }
    )

    assert repository.external_repository_id == "77"
    assert repository.permission == "write"
    assert repository.owner_name == "alice"


def test_repository_metadata_rejects_incomplete_payload() -> None:
    with pytest.raises(GiteaOAuthClientError, match="repository невалиден"):
        _repository({"id": 77, "name": "missing-owner-and-url"})
