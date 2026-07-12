import pytest

from webapp.api.server import _dev_skip_auth


@pytest.mark.parametrize("requested", ["1", "true", "yes", "TRUE", " YES "])
def test_skip_auth_requires_explicit_development_environment(requested: str) -> None:
    assert _dev_skip_auth({"HEREASSISTANT_ENV": "development", "WEBAPP_DEV_SKIP_AUTH": requested})


@pytest.mark.parametrize("environment", [None, "production", "staging", "test"])
def test_skip_auth_never_activates_outside_development(environment: str | None) -> None:
    env = {"WEBAPP_DEV_SKIP_AUTH": "1"}
    if environment is not None:
        env["HEREASSISTANT_ENV"] = environment

    assert not _dev_skip_auth(env)


@pytest.mark.parametrize("requested", [None, "", "0", "false", "no", "enabled"])
def test_development_still_requires_explicit_skip_flag(requested: str | None) -> None:
    env = {"HEREASSISTANT_ENV": "development"}
    if requested is not None:
        env["WEBAPP_DEV_SKIP_AUTH"] = requested

    assert not _dev_skip_auth(env)
