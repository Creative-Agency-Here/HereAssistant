import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from core import config
from webapp.api.auth import validate_init_data

TOKEN = "123456:test-token-for-local-tests"


def signed_init_data(
    *,
    token: str = TOKEN,
    user: object | None = None,
    auth_date: int | str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    fields: dict[str, str] = {"query_id": "test-query"}
    if user is not None:
        fields["user"] = json.dumps(user, ensure_ascii=False, separators=(",", ":"))
    if auth_date is not None:
        fields["auth_date"] = str(auth_date)
    if extra:
        fields.update(extra)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture(autouse=True)
def auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setattr(config, "ADMIN_IDS", [42])
    monkeypatch.delenv("ADMIN_IDS", raising=False)


def test_valid_init_data_returns_unicode_user() -> None:
    user = {"id": 42, "first_name": "Илья 🚀", "username": "here"}

    assert validate_init_data(signed_init_data(user=user, auth_date=int(time.time()))) == user


def test_signature_field_participates_in_hash() -> None:
    user = {"id": 42, "first_name": "User"}
    data = signed_init_data(
        user=user,
        auth_date=int(time.time()),
        extra={"signature": "ed25519-signature-placeholder"},
    )

    assert validate_init_data(data) == user


@pytest.mark.parametrize("init_data", ["", "query_id=x", "hash="])
def test_empty_or_unsigned_data_is_rejected(init_data: str) -> None:
    assert validate_init_data(init_data) is None


def test_wrong_bot_token_signature_is_rejected() -> None:
    data = signed_init_data(
        token="999999:wrong-token",
        user={"id": 42},
        auth_date=int(time.time()),
    )

    assert validate_init_data(data) is None


def test_tampered_signed_field_is_rejected() -> None:
    data = signed_init_data(user={"id": 42}, auth_date=int(time.time()))

    assert validate_init_data(data.replace("test-query", "tampered-query")) is None


def test_duplicate_parameters_are_rejected_even_with_collapsed_valid_hash() -> None:
    user = json.dumps({"id": 42}, separators=(",", ":"))
    collapsed = {
        "auth_date": str(int(time.time())),
        "query_id": "second-query",
        "user": user,
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(collapsed.items()))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    valid_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data = urlencode(
        [
            ("query_id", "first-query"),
            ("query_id", "second-query"),
            ("user", user),
            ("auth_date", collapsed["auth_date"]),
            ("hash", valid_hash),
        ]
    )

    assert validate_init_data(data) is None


def test_missing_telegram_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "")

    assert validate_init_data("anything") is None


def test_missing_user_is_rejected() -> None:
    assert validate_init_data(signed_init_data(auth_date=int(time.time()))) is None


def test_invalid_user_json_is_rejected() -> None:
    assert (
        validate_init_data(
            signed_init_data(user={"id": 42}, auth_date=int(time.time())).replace(
                "%7B%22id%22%3A42%7D", "%7Bbroken"
            )
        )
        is None
    )


@pytest.mark.parametrize("user_id", [7, "42", None])
def test_non_admin_or_non_integer_user_id_is_rejected(user_id: object) -> None:
    data = signed_init_data(user={"id": user_id}, auth_date=int(time.time()))

    assert validate_init_data(data) is None


def test_admin_from_environment_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    monkeypatch.setenv("ADMIN_IDS", "7, 42")
    user = {"id": 42, "first_name": "Environment Admin"}

    assert validate_init_data(signed_init_data(user=user, auth_date=int(time.time()))) == user


def test_expired_init_data_is_rejected() -> None:
    data = signed_init_data(user={"id": 42}, auth_date=int(time.time()) - 101)

    assert validate_init_data(data, max_age_sec=100) is None


@pytest.mark.parametrize("auth_date", [None, "not-a-timestamp"])
def test_missing_or_invalid_auth_date_is_rejected(auth_date: int | str | None) -> None:
    data = signed_init_data(user={"id": 42}, auth_date=auth_date)

    assert validate_init_data(data) is None


def test_auth_date_too_far_in_future_is_rejected() -> None:
    data = signed_init_data(user={"id": 42}, auth_date=int(time.time()) + 301)

    assert validate_init_data(data) is None
