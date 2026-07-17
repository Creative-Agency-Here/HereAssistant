from __future__ import annotations

import pytest

from core import config, herecrm_client


def test_endpoint_preserves_api_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "HERECRM_SYNC_URL", "https://crm.example.com/api/v1")
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_test")

    assert (
        herecrm_client.endpoint("conversations")
        == "https://crm.example.com/api/v1/hereassistant-sync/conversations"
    )


@pytest.mark.parametrize("url", ["", "http://crm.example.com", "crm.example.com"])
def test_endpoint_fails_closed_without_absolute_https_url(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(config, "HERECRM_SYNC_URL", url)
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "has_test")

    with pytest.raises(herecrm_client.HereCrmClientError) as raised:
        herecrm_client.endpoint("digest")

    assert raised.value.code == "crm_not_configured"
    assert raised.value.status == 503


def test_endpoint_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "HERECRM_SYNC_URL", "https://crm.example.com")
    monkeypatch.setattr(config, "HERECRM_SYNC_TOKEN", "")

    with pytest.raises(herecrm_client.HereCrmClientError):
        herecrm_client.endpoint("conversations")
