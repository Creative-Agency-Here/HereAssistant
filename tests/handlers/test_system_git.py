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
            {
                "status": "active",
                "user_id": user_id,
                "provider": "gitea",
                "host": "git.example.com",
                "external_login": "alice",
            },
            {
                "status": "expired",
                "user_id": user_id,
                "provider": "github",
                "host": "github.com",
                "external_login": "alice-work",
            },
        ],
    )

    await system.cmd_git(message)  # type: ignore[arg-type]

    text, markup = message.answers[0]
    assert "Подключено: 1" in text
    assert "требуют обновления: 1" in text
    assert "Gitea · git.example.com · alice — подключён" in text
    assert "GitHub · github.com · alice-work — нужно обновить доступ" in text
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


def test_git_connection_line_normalizes_untrusted_metadata() -> None:
    line = system._git_connection_line(  # noqa: SLF001
        {
            "provider": "gitea\nspoofed",
            "host": "git.example.com\r\nsecond-line",
            "external_login": "  alice\tadmin  ",
            "status": "active",
        }
    )

    assert "\n" not in line
    assert "\r" not in line
    assert "gitea spoofed · git.example.com second-line · alice admin" in line
