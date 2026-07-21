import asyncio
from pathlib import Path
from typing import Any

import pytest

from providers import qwen_code
from providers.qwen_code import QwenCodeProvider, _approval_mode

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "qwen_success.jsonl"


@pytest.fixture(autouse=True)
def bypass_real_cli_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qwen_code, "resolve_cli_argv", lambda argv, **_kwargs: argv)


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
        self.pid = 654

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


def make_provider(tmp_path: Path) -> QwenCodeProvider:
    account: Any = {"cli_home_path": str(tmp_path / "qwen-home")}
    return QwenCodeProvider(account)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "auto"),
        ("plan", "plan"),
        ("default", "default"),
        ("auto-edit", "auto-edit"),
        ("auto", "auto"),
        ("yolo", "auto"),
        ("unknown", "auto"),
    ],
)
def test_approval_mode_allowlist(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: str
) -> None:
    if configured is None:
        monkeypatch.delenv("QWEN_APPROVAL_MODE", raising=False)
    else:
        monkeypatch.setenv("QWEN_APPROVAL_MODE", configured)
    assert _approval_mode() == expected


def test_env_isolates_qwen_config_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAILIAN_CODING_PLAN_API_KEY", "foreign-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foreign.invalid/v1")
    environment = make_provider(tmp_path).env()
    expected = str(tmp_path / "qwen-home" / ".qwen")
    assert environment["QWEN_HOME"] == expected
    assert environment["QWEN_RUNTIME_DIR"] == expected
    assert environment["QWEN_TELEMETRY_ENABLED"] == "false"
    assert environment["HEREASSISTANT_PROVIDER"] == "qwen_code"
    assert "BAILIAN_CODING_PLAN_API_KEY" not in environment
    assert "OPENAI_BASE_URL" not in environment


@pytest.mark.asyncio
async def test_runtime_streams_and_resumes_native_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(FIXTURE.read_bytes())
    spawn_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_spawn(*args: object, **kwargs: object) -> FakeProcess:
        spawn_calls.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    progress: list[str] = []

    async def on_progress(text: str, event_type: str, meta: Any) -> None:
        del text, meta
        progress.append(event_type)

    text, session_id, meta = await make_provider(tmp_path)._run_streaming(
        ["qwen", "--output-format", "stream-json"],
        str(tmp_path),
        None,
        on_progress,
        stdin_data="приватный prompt",
    )

    assert text == "Готово"
    assert session_id == "qwen-session"
    assert meta.get("tokens_in") == 80
    assert meta.get("tokens_out") == 12
    assert meta.get("edits", [])[0]["file"] == "/tmp/demo.py"
    assert progress == ["tool_start", "tool_result", "partial_delta", "assistant_delta"]
    assert process.stdin.data == "приватный prompt".encode()
    assert process.stdin.closed
    assert spawn_calls[0][0] == ("qwen", "--output-format", "stream-json")


@pytest.mark.asyncio
async def test_runtime_reports_bounded_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(b"", stderr=("ошибка" * 500).encode(), returncode=2)

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    with pytest.raises(RuntimeError) as caught:
        await make_provider(tmp_path)._run_streaming(["qwen"], str(tmp_path), None, None)

    assert str(caught.value).startswith("qwen failed (rc=2): ошибка")
    assert len(str(caught.value)) < 2100


@pytest.mark.asyncio
async def test_runtime_reports_structured_error_even_with_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = (
        '{"type":"result","subtype":"error_during_execution","is_error":true,'
        '"error":{"message":"No auth type is selected"}}\n'
    ).encode()
    process = FakeProcess(event, returncode=0)

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    with pytest.raises(RuntimeError, match="No auth type is selected"):
        await make_provider(tmp_path)._run_streaming(["qwen"], str(tmp_path), None, None)
