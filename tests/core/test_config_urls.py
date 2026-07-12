import pytest

from core import config


def test_webapp_url_joins_subpath_without_double_slashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "WEBAPP_URL", "https://example.com/webapp/")
    monkeypatch.setattr(config, "WEBAPP_ACCESS_KEY", "key")

    assert config.webapp_url() == "https://example.com/webapp/"
    assert config.webapp_url("/edits") == "https://example.com/webapp/edits"
    assert config.webapp_url(include_access_key=True) == "https://example.com/webapp/?key=key"


def test_webapp_url_is_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "WEBAPP_URL", "")

    assert config.webapp_url("/history") == ""


def test_os_runner_map_is_explicit_and_typed() -> None:
    assert config._parse_os_runner_map("100:ha-ilya,200:ha-pavel") == {
        100: "ha-ilya",
        200: "ha-pavel",
    }


@pytest.mark.parametrize(
    "raw",
    ["100", "abc:ha-user", "100:root", "100:BadUser", "100:ha-a,100:ha-b"],
)
def test_os_runner_map_rejects_unsafe_entries(raw: str) -> None:
    with pytest.raises(ValueError):
        config._parse_os_runner_map(raw)
