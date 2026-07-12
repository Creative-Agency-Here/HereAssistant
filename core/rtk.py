"""Изолированная RTK-статистика без раскрытия истории команд."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from . import config, db

CLAUDE_HOOK_COMMAND = "rtk hook claude"
SAFE_CLAUDE_RULES = (
    "Bash(rtk git status:*)",
    "Bash(rtk git diff:*)",
    "Bash(rtk git log:*)",
    "Bash(rtk ls:*)",
    "Bash(rtk find:*)",
    "Bash(rtk grep:*)",
    "Bash(rtk read:*)",
    "Bash(rtk pytest:*)",
    "Bash(rtk ruff check:*)",
    "Bash(rtk tsc:*)",
    "Bash(rtk vitest:*)",
    "Bash(rtk playwright test:*)",
)


class Savings(TypedDict):
    available: bool
    accounts: int
    commands: int
    input_tokens: int
    output_tokens: int
    saved_tokens: int
    savings_pct: float
    today_commands: int
    today_saved_tokens: int


def runtime_dir(cli_home: str | Path) -> Path:
    return Path(cli_home) / ".rtk"


def runtime_env(cli_home: str | Path) -> dict[str, str]:
    """Возвращает per-account env, не меняя HOME/SSH/Git поведение провайдера."""
    directory = runtime_dir(cli_home)
    tee = directory / "tee"
    tee.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    tee.chmod(0o700)
    return {
        "RTK_DB_PATH": str(directory / "history.db"),
        "RTK_TEE_DIR": str(tee),
        "RTK_TELEMETRY_DISABLED": "1",
    }


def configure_claude_profile(cli_home: str | Path) -> bool:
    """Идемпотентно подключает pinned native hook и точечные permission rules."""
    if shutil.which("rtk") is None:
        return False
    home = Path(cli_home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = home / "settings.json"
    try:
        payload = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    except (OSError, json.JSONDecodeError):
        return False
    hooks = payload.setdefault("hooks", {})
    pre_tool = hooks.setdefault("PreToolUse", [])
    installed = any(
        hook.get("command") == CLAUDE_HOOK_COMMAND
        for entry in pre_tool
        if isinstance(entry, dict)
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    )
    if not installed:
        pre_tool.append(
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": CLAUDE_HOOK_COMMAND}],
            }
        )
    permissions = payload.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    for rule in SAFE_CLAUDE_RULES:
        if rule not in allow:
            allow.append(rule)
    temporary = settings.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(settings)
        home.chmod(0o700)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def sanitize_runtime(cli_home: str | Path) -> None:
    """Удаляет аргументы команд, project paths и raw tee после provider run."""
    directory = runtime_dir(cli_home)
    database = directory / "history.db"
    if database.exists():
        try:
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """UPDATE commands SET
                       original_cmd=CASE
                         WHEN instr(original_cmd, ' ') > 0
                         THEN substr(original_cmd, 1, instr(original_cmd, ' ') - 1)
                         ELSE original_cmd END,
                       rtk_cmd=CASE
                         WHEN rtk_cmd LIKE 'rtk % %'
                         THEN substr(rtk_cmd, 1,
                              instr(rtk_cmd, ' ') + instr(substr(rtk_cmd, instr(rtk_cmd, ' ') + 1), ' ') - 1)
                         ELSE rtk_cmd END,
                       project_path=''"""
                )
            database.chmod(0o600)
        except (OSError, sqlite3.DatabaseError):
            # Метрики best effort и не должны ломать ответ провайдера.
            pass
    tee = directory / "tee"
    if tee.is_dir():
        for item in tee.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except OSError:
                pass


def _aggregate(database: Path, since: str | None = None) -> tuple[int, int, int, int]:
    if not database.is_file():
        return (0, 0, 0, 0)
    query = "SELECT COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(saved_tokens),0) FROM commands"
    params: tuple[str, ...] = ()
    if since:
        query += " WHERE timestamp>=?"
        params = (since,)
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            row = connection.execute(query, params).fetchone()
    except (OSError, sqlite3.DatabaseError):
        return (0, 0, 0, 0)
    return tuple(int(value or 0) for value in row)  # type: ignore[return-value]


def _runner_aggregate(user_id: int, provider: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    path = config.OS_RUNNER_METRICS_DIR / str(user_id) / f"{provider}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        total = tuple(
            int(payload.get(key, 0))
            for key in ("commands", "input_tokens", "output_tokens", "saved_tokens")
        )
        daily = (
            int(payload.get("today_commands", 0)),
            0,
            0,
            int(payload.get("today_saved_tokens", 0)),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return (0, 0, 0, 0), (0, 0, 0, 0)
    if any(value < 0 for value in (*total, *daily)):
        return (0, 0, 0, 0), (0, 0, 0, 0)
    return total, daily


def user_savings(user_id: int) -> Savings:
    """Суммирует только enabled-аккаунты владельца; shared нельзя атрибутировать."""
    with db.conn() as connection:
        accounts = [
            (str(row["provider"]), Path(row["cli_home_path"]))
            for row in connection.execute(
                """SELECT provider,cli_home_path FROM accounts
                   WHERE enabled=1 AND owner_user_id=? ORDER BY id""",
                (user_id,),
            )
        ]
    if config.OS_RUNNERS_ENABLED:
        snapshots = [_runner_aggregate(user_id, provider) for provider in dict(accounts)]
        total = [snapshot[0] for snapshot in snapshots]
        daily = [snapshot[1] for snapshot in snapshots]
    else:
        today = datetime.now(UTC).date().isoformat()
        total = [_aggregate(runtime_dir(home) / "history.db") for _, home in accounts]
        daily = [_aggregate(runtime_dir(home) / "history.db", today) for _, home in accounts]
    commands = sum(row[0] for row in total)
    input_tokens = sum(row[1] for row in total)
    output_tokens = sum(row[2] for row in total)
    saved_tokens = sum(row[3] for row in total)
    return Savings(
        available=shutil.which("rtk") is not None,
        accounts=len(accounts),
        commands=commands,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        saved_tokens=saved_tokens,
        savings_pct=round(saved_tokens * 100 / input_tokens, 1) if input_tokens else 0.0,
        today_commands=sum(row[0] for row in daily),
        today_saved_tokens=sum(row[3] for row in daily),
    )
