import asyncio
from pathlib import Path
from typing import Any

import pytest

from providers import gemini
from providers.gemini import GeminiProvider

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "gemini_success.jsonl"


@pytest.fixture(autouse=True)
def bypass_real_cli_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime tests own the fake process; argv resolution has separate contract tests."""
    monkeypatch.setattr(gemini, "resolve_cli_argv", lambda argv, **_kwargs: argv)


class FakeStdin:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, stdout: bytes, *, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.stdin = FakeStdin()
        self.returncode = returncode
        self.pid = 456

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


def make_provider(tmp_path: Path) -> GeminiProvider:
    account: Any = {"cli_home_path": str(tmp_path / "gemini-home")}
    return GeminiProvider(account)


@pytest.mark.asyncio
async def test_runtime_feeds_jsonl_to_parser_and_preserves_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(FIXTURE.read_bytes())
    spawn_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_spawn(*args: object, **kwargs: object) -> FakeProcess:
        spawn_calls.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    progress: list[tuple[str, str, dict[str, object]]] = []

    async def on_progress(text: str, event_type: str, meta: Any) -> None:
        progress.append((text, event_type, dict(meta)))

    text, session_id, meta = await make_provider(tmp_path)._run_streaming(
        ["gemini", "-o", "stream-json"],
        str(tmp_path),
        None,
        on_progress,
        stdin_data="приватный prompt",
    )

    assert text == "Готово"
    assert session_id is None
    assert meta.get("tokens_in") == 40
    assert [item[1] for item in progress] == ["partial_delta", "partial_delta", "tool_use"]
    assert process.stdin.data == "приватный prompt".encode()
    assert process.stdin.closed
    assert spawn_calls[0][0] == ("gemini", "-o", "stream-json")
    assert spawn_calls[0][1]["limit"] == 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_runtime_reports_nonzero_exit_with_bounded_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(b"", stderr=("ошибка" * 500).encode(), returncode=2)

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    with pytest.raises(RuntimeError) as caught:
        await make_provider(tmp_path)._run_streaming(["gemini"], str(tmp_path), None, None)

    message = str(caught.value)
    assert message.startswith("gemini failed (rc=2): ошибка")
    assert len(message) < 2100
