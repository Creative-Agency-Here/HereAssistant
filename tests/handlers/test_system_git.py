from types import SimpleNamespace

import pytest

from handlers import system


class FakeMessage:
    def __init__(self, user_id: int = 100) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple[str, object | None]] = []

    async def answer(self, text: str, reply_markup: object | None = None) -> None:
        self.answers.append((text, reply_markup))


async def test_git_command_opens_owner_settings_without_access_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = FakeMessage()
    monkeypatch.setattr(system, "is_allowed", lambda _message: True)
    monkeypatch.setattr(system.config, "WEBAPP_URL", "https://assistant.example/app")
    monkeypatch.setattr(system.config, "WEBAPP_ACCESS_KEY", "must-not-leak")
    monkeypatch.setattr(
        system.git_connections,
        "list_connections",
        lambda user_id: [
            {"status": "active", "user_id": user_id},
            {"status": "expired", "user_id": user_id},
        ],
    )

    await system.cmd_git(message)  # type: ignore[arg-type]

    text, markup = message.answers[0]
    assert "Подключено: 1" in text
    assert "требуют обновления: 1" in text
    assert "must-not-leak" not in text
    button_url = markup.inline_keyboard[0][0].web_app.url  # type: ignore[union-attr]
    assert button_url == "https://assistant.example/app/settings"
    assert "must-not-leak" not in button_url


async def test_git_command_fails_closed_without_webapp_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = FakeMessage()
    monkeypatch.setattr(system, "is_allowed", lambda _message: True)
    monkeypatch.setattr(system.config, "WEBAPP_URL", "")

    await system.cmd_git(message)  # type: ignore[arg-type]

    assert "недоступны" in message.answers[0][0]
