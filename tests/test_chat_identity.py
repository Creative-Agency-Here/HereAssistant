from typing import cast

from chat_identity import UserRecord, find_user, user_display


def users() -> list[UserRecord]:
    return cast(
        list[UserRecord],
        [
            {"telegram_id": 10, "username": "Alice"},
            {"telegram_id": 20, "username": None},
            {"telegram_id": 30, "username": "Илья"},
        ],
    )


def test_user_display_prefers_username_and_falls_back_to_id() -> None:
    records = users()

    assert user_display(records[0]) == "@Alice"
    assert user_display(records[1]) == "20"


def test_find_user_supports_id_at_prefix_and_case_insensitive_unicode() -> None:
    records = users()

    assert find_user(records, "20") is records[1]
    assert find_user(records, "@ALICE") is records[0]
    assert find_user(records, "@илья") is records[2]
    assert find_user(records, "missing") is None
