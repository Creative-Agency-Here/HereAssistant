import pytest

from webapp.api.gitea_oauth import (
    MAX_RESPONSE_BYTES,
    REPOSITORIES_PER_PAGE,
    GiteaOAuthClientError,
    _json_response,
    _repository,
)


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


@pytest.mark.asyncio
async def test_repository_page_is_bounded_and_reports_oversize_without_body() -> None:
    class Content:
        remaining = MAX_RESPONSE_BYTES + 1

        async def read(self, limit: int) -> bytes:
            size = min(limit, self.remaining)
            self.remaining -= size
            return b"x" * size

    class Response:
        status = 200
        content = Content()

    assert REPOSITORIES_PER_PAGE == 10
    with pytest.raises(GiteaOAuthClientError) as captured:
        await _json_response(Response(), "repositories")
    assert captured.value.stage == "repositories"
    assert captured.value.status == 200
    assert captured.value.reason == "response_too_large"


@pytest.mark.asyncio
async def test_json_response_joins_fragmented_network_chunks() -> None:
    class Content:
        chunks = [b'[{"id":', b"77}]", b""]

        async def read(self, _limit: int) -> bytes:
            return self.chunks.pop(0)

    class Response:
        status = 200
        content = Content()

    assert await _json_response(Response(), "repositories") == [{"id": 77}]
