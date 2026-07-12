#!/usr/bin/python3
"""Validated OS-user boundary for provider CLI processes.

Production installs a root-owned copy as `/usr/local/libexec/hereassistant-runner`.
The application may choose arguments, but this wrapper restricts identity, provider
profile and cwd using root-owned `/etc/hereassistant/runners/<unix-user>.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import pwd
except ImportError:  # Windows imports the module for CI, but cannot execute it.
    pwd = None  # type: ignore[assignment]

CONFIG_DIR = Path("/etc/hereassistant/runners")
PROVIDER_COMMANDS = {
    "claude_code": "claude",
    "codex": "codex",
    "gemini": "gemini",
}


class RunnerDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerProfile:
    cli_home: Path
    metrics_file: Path


@dataclass(frozen=True)
class RunnerConfig:
    user_id: int
    unix_user: str
    home: Path
    path: str
    providers: dict[str, RunnerProfile]
    project_roots: tuple[Path, ...]
    git_allowed_hosts: tuple[str, ...]


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def load_config(unix_user: str, *, config_dir: Path = CONFIG_DIR) -> RunnerConfig:
    path = config_dir / f"{unix_user}.json"
    try:
        stat = path.stat()
    except OSError as error:
        raise RunnerDenied("runner config отсутствует") from error
    if stat.st_uid != 0 or stat.st_mode & 0o022:
        raise RunnerDenied("runner config должен принадлежать root и быть non-writable")
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        user_id = int(raw["user_id"])
        configured_user = str(raw["unix_user"])
        home = Path(raw["home"]).resolve(strict=True)
        provider_values: dict[str, dict[str, str]] = dict(raw["providers"])
        root_values = list(raw["project_roots"])
        git_hosts = tuple(str(value).lower() for value in raw.get("git_allowed_hosts", []))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise RunnerDenied("runner config повреждён") from error
    if configured_user != unix_user:
        raise RunnerDenied("unix_user не совпадает с именем runner config")
    providers: dict[str, RunnerProfile] = {}
    for name, value in provider_values.items():
        try:
            cli_home = Path(value["cli_home"]).resolve(strict=True)
            metrics_file = Path(value["metrics_file"])
        except (KeyError, TypeError, OSError) as error:
            raise RunnerDenied("runner provider profile повреждён") from error
        if not metrics_file.is_absolute():
            raise RunnerDenied("runner metrics_file должен быть абсолютным")
        providers[name] = RunnerProfile(cli_home=cli_home, metrics_file=metrics_file)
    if not providers or any(name not in PROVIDER_COMMANDS for name in providers):
        raise RunnerDenied("runner providers невалидны")
    roots = tuple(Path(value).resolve(strict=True) for value in root_values)
    if not roots:
        raise RunnerDenied("runner project_roots пуст")
    return RunnerConfig(
        user_id=user_id,
        unix_user=unix_user,
        home=home,
        path=str(raw.get("path") or "/usr/local/bin:/usr/bin:/bin"),
        providers=providers,
        project_roots=roots,
        git_allowed_hosts=git_hosts,
    )


def validate_request(
    config: RunnerConfig,
    *,
    user_id: int,
    provider: str,
    cli_home: str,
    cwd: str,
    command: list[str],
) -> tuple[Path, Path]:
    if user_id != config.user_id:
        raise RunnerDenied("Telegram user_id не совпадает с runner")
    profile = config.providers.get(provider)
    if profile is None:
        raise RunnerDenied("provider не разрешён runner config")
    actual_home = Path(cli_home).resolve(strict=True)
    if actual_home != profile.cli_home:
        raise RunnerDenied("cli_home не совпадает с runner config")
    actual_cwd = Path(cwd).resolve(strict=True)
    if not any(_inside(actual_cwd, root) for root in config.project_roots):
        raise RunnerDenied("cwd вне разрешённых project_roots")
    if not command or command[0] != PROVIDER_COMMANDS[provider]:
        raise RunnerDenied("provider executable не разрешён")
    return actual_home, actual_cwd


def validate_git_request(
    config: RunnerConfig, *, user_id: int, cwd: str, command: list[str]
) -> Path:
    actual_cwd = Path(cwd).resolve(strict=True)
    if user_id != config.user_id or not any(
        _inside(actual_cwd, root) for root in config.project_roots
    ):
        raise RunnerDenied("Git identity/cwd запрещены")
    if command in (
        ["git", "remote"],
        ["git", "status", "--short", "--branch"],
        ["git", "pull", "--ff-only"],
    ):
        return actual_cwd
    if (
        len(command) == 4
        and command[:2] == ["git", "push"]
        and command[2] in ("origin", "github")
        and command[3] == "HEAD"
    ):
        return actual_cwd
    if len(command) == 5 and command[:3] == ["git", "clone", "--"]:
        if command[3].startswith("git@"):
            host = command[3][4:].split(":", 1)[0].lower()
        else:
            url = urlsplit(command[3])
            if url.scheme != "https" or url.username or url.password:
                raise RunnerDenied("Git URL запрещён")
            host = (url.hostname or "").lower()
        if host not in config.git_allowed_hosts:
            raise RunnerDenied("Git host запрещён")
        target = Path(command[4])
    elif len(command) == 6 and command[:4] == ["git", "worktree", "add", "-b"]:
        if not all(char.isalnum() or char in "-_" for char in command[4]):
            raise RunnerDenied("Git branch запрещена")
        target = Path(command[5])
    else:
        raise RunnerDenied("Git command не входит в allowlist")
    resolved_target = target.parent.resolve(strict=True) / target.name
    if not any(_inside(resolved_target, root) for root in config.project_roots):
        raise RunnerDenied("Git destination вне project_roots")
    return actual_cwd


def provider_environment(config: RunnerConfig, provider: str, cli_home: Path) -> dict[str, str]:
    environment = {
        "HOME": str(config.home),
        "USER": config.unix_user,
        "LOGNAME": config.unix_user,
        "PATH": config.path,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "RTK_TELEMETRY_DISABLED": "1",
        "RTK_DB_PATH": str(cli_home / ".rtk" / "history.db"),
        "RTK_TEE_DIR": str(cli_home / ".rtk" / "tee"),
    }
    if provider == "claude_code":
        environment["CLAUDE_CONFIG_DIR"] = str(cli_home)
    elif provider == "codex":
        environment["CODEX_HOME"] = str(cli_home)
    elif provider == "gemini":
        environment["HOME"] = str(cli_home)
        environment["USERPROFILE"] = str(cli_home)
        environment["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    return environment


def sanitize_rtk(cli_home: Path) -> None:
    database = cli_home / ".rtk" / "history.db"
    if database.is_file():
        try:
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """UPDATE commands SET
                       original_cmd=CASE WHEN instr(original_cmd, ' ') > 0
                         THEN substr(original_cmd, 1, instr(original_cmd, ' ') - 1)
                         ELSE original_cmd END,
                       rtk_cmd=CASE WHEN rtk_cmd LIKE 'rtk % %'
                         THEN substr(rtk_cmd, 1,
                              instr(rtk_cmd, ' ') + instr(substr(rtk_cmd, instr(rtk_cmd, ' ') + 1), ' ') - 1)
                         ELSE rtk_cmd END,
                       project_path=''"""
                )
            database.chmod(0o600)
        except (OSError, sqlite3.DatabaseError):
            pass
    tee = cli_home / ".rtk" / "tee"
    if tee.is_dir():
        for item in tee.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except OSError:
                pass


def write_rtk_metrics(cli_home: Path, metrics_file: Path) -> None:
    database = cli_home / ".rtk" / "history.db"
    total = (0, 0, 0, 0)
    today = (0, 0)
    if database.is_file():
        try:
            with sqlite3.connect(database) as connection:
                total = tuple(
                    int(value or 0)
                    for value in connection.execute(
                        """SELECT COUNT(*),COALESCE(SUM(input_tokens),0),
                                  COALESCE(SUM(output_tokens),0),COALESCE(SUM(saved_tokens),0)
                           FROM commands"""
                    ).fetchone()
                )
                today = tuple(
                    int(value or 0)
                    for value in connection.execute(
                        """SELECT COUNT(*),COALESCE(SUM(saved_tokens),0) FROM commands
                           WHERE timestamp>=?""",
                        (datetime.now(UTC).date().isoformat(),),
                    ).fetchone()
                )
        except sqlite3.DatabaseError:
            pass
    payload = {
        "commands": total[0],
        "input_tokens": total[1],
        "output_tokens": total[2],
        "saved_tokens": total[3],
        "today_commands": today[0],
        "today_saved_tokens": today[1],
    }
    try:
        metrics_file.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temporary = metrics_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.chmod(0o640)
        temporary.replace(metrics_file)
    except OSError:
        pass


def run(
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
    cli_home: Path,
    metrics_file: Path,
) -> int:
    runtime = cli_home / ".rtk"
    (runtime / "tee").mkdir(parents=True, exist_ok=True, mode=0o700)
    process = subprocess.Popen(command, cwd=cwd, env=environment, start_new_session=True)

    def forward(signum: int, _frame: object) -> None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    previous = {
        signum: signal.signal(signum, forward) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        return process.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        sanitize_rtk(cli_home)
        write_rtk_metrics(cli_home, metrics_file)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--provider", choices=sorted(PROVIDER_COMMANDS))
    mode.add_argument("--git", action="store_true")
    parser.add_argument("--cli-home")
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if pwd is None:
        print("runner denied: Unix is required", file=sys.stderr)
        return 77
    unix_user = pwd.getpwuid(os.getuid()).pw_name
    try:
        config = load_config(unix_user)
        if args.git:
            cwd = validate_git_request(config, user_id=args.user_id, cwd=args.cwd, command=command)
            cli_home = None
        else:
            if not args.provider or not args.cli_home:
                raise RunnerDenied("provider/cli_home обязательны")
            cli_home, cwd = validate_request(
                config,
                user_id=args.user_id,
                provider=args.provider,
                cli_home=args.cli_home,
                cwd=args.cwd,
                command=command,
            )
    except RunnerDenied as error:
        print(f"runner denied: {error}", file=sys.stderr)
        return 77
    if args.git:
        return subprocess.call(
            command,
            cwd=cwd,
            env={
                "HOME": str(config.home),
                "USER": config.unix_user,
                "LOGNAME": config.unix_user,
                "PATH": config.path,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            },
        )
    assert args.provider and cli_home is not None
    return run(
        command,
        cwd,
        provider_environment(config, args.provider, cli_home),
        cli_home,
        config.providers[args.provider].metrics_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
