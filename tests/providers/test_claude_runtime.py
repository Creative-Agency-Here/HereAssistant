import asyncio
from pathlib import Path
from typing import Any

import pytest

from providers.claude_code import ClaudeCodeProvider, _permission_mode

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "claude_success.jsonl"


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
        self.pid = 123
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


class HangingReader:
    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        return b""


class HangingProcess:
    def __init__(self) -> None:
        self.stdout = HangingReader()
        self.stderr = HangingReader()
        self.stdin = None
        self.returncode: int | None = None
        self.pid = 789
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or -9


def make_provider(tmp_path: Path) -> ClaudeCodeProvider:
    account: Any = {"cli_home_path": str(tmp_path / "claude-home")}
    return ClaudeCodeProvider(account)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "acceptEdits"),
        ("acceptEdits", "acceptEdits"),
        ("default", "default"),
        ("bypassPermissions", "acceptEdits"),
        ("unknown", "acceptEdits"),
    ],
)
def test_permission_mode_allowlist(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: str
) -> None:
    if configured is None:
        monkeypatch.delenv("CLAUDE_PERMISSION_MODE", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_PERMISSION_MODE", configured)

    assert _permission_mode() == expected


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

    result = await make_provider(tmp_path)._run_streaming(
        ["claude", "--print"],
        str(tmp_path),
        None,
        on_progress,
        stdin_data="приватный prompt",
    )

    text, session_id, meta = result
    assert text == "Готово"
    assert session_id == "session-final"
    assert meta.get("tokens_in") == 120
    assert [item[1] for item in progress] == [
        "thinking_delta",
        "tool_start",
        "tool_result",
        "partial_delta",
    ]
    assert process.stdin.data == "приватный prompt".encode()
    assert process.stdin.closed
    assert spawn_calls[0][0] == ("claude", "--print")
    assert spawn_calls[0][1]["limit"] == 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_runtime_returns_partial_text_on_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = (
        '{"type":"stream_event","event":{"type":"content_block_delta",'
        '"delta":{"text":"часть"}}}\n'
        '{"type":"rate_limit_event","rate_limit":{"resets_at":"12:30"}}\n'
    ).encode()
    process = FakeProcess(events, returncode=1)

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    text, _session_id, meta = await make_provider(tmp_path)._run_streaming(
        ["claude"], str(tmp_path), None, None
    )

    assert text.startswith("часть")
    assert "rate limit" in text
    assert meta.get("partial_due_to_error") is True


@pytest.mark.asyncio
async def test_runtime_skips_malformed_json_and_non_object_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = (
        'not-json\n[]\n{"type":"result","result":"fallback","usage":{"input_tokens":1}}\n'
    ).encode()
    process = FakeProcess(events)

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    text, _session_id, meta = await make_provider(tmp_path)._run_streaming(
        ["claude"], str(tmp_path), None, None
    )

    assert text == "fallback"
    assert meta.get("tokens_in") == 1


@pytest.mark.asyncio
async def test_cancellation_kills_running_cli_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = HangingProcess()

    async def fake_spawn(*_args: object, **_kwargs: object) -> HangingProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    task = asyncio.create_task(
        make_provider(tmp_path)._run_streaming(["claude"], str(tmp_path), None, None)
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed
    assert process.returncode == -9
