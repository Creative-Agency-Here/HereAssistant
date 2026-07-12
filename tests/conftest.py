from collections.abc import Iterator

import pytest

from core import project_config


@pytest.fixture(autouse=True)
def reset_project_policy_cache() -> Iterator[None]:
    """Каждый тест политики начинает с чистого process-local кэша."""
    project_config._cache.clear()
    project_config._missing_cache.clear()
    yield
    project_config._cache.clear()
    project_config._missing_cache.clear()
