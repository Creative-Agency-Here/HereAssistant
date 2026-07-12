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
