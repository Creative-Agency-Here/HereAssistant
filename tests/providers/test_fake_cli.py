import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from providers.base import CLIProvider

FIXTURE = Path(__file__).parents[1] / "fixtures" / "providers" / "fake_cli.py"


def provider(tmp_path: Path) -> CLIProvider:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT ? AS cli_home_path, 'fake' AS provider, 'fake' AS label",
        (str(tmp_path / "home"),),
    ).fetchone()
    assert row is not None
    return CLIProvider(row)


@pytest.mark.asyncio
async def test_cross_platform_fake_cli_round_trips_args_and_stdin(tmp_path: Path) -> None:
    current = provider(tmp_path)

    code, stdout, stderr = await current._exec(
        [sys.executable, str(FIXTURE), "echo", "аргумент"],
        str(tmp_path),
        stdin_data="ввод",
    )

    assert code == 0 and stderr == ""
    assert json.loads(stdout) == {"args": ["аргумент"], "stdin": "ввод"}


@pytest.mark.asyncio
async def test_subprocess_log_never_contains_prompt_stdin_or_cwd(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    current = provider(tmp_path)
    secret = "PRIVATE_PROMPT_VALUE"
    private_cwd = tmp_path / "private-project"
    private_cwd.mkdir()
    with caplog.at_level(logging.INFO, logger="bridge.provider"):
        await current._exec(
            [sys.executable, str(FIXTURE), "echo", secret],
            str(private_cwd),
            stdin_data=secret,
        )

    logs = caplog.text
    assert secret not in logs
    assert "private-project" not in logs
