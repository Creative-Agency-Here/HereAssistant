#!/usr/bin/python3
"""Validated OS-user boundary for provider CLI processes.

Production installs a root-owned copy as `/usr/local/libexec/hereassistant-runner`.
The application may choose arguments, but this wrapper restricts identity, provider
profile and cwd using root-owned `/etc/hereassistant/runners/<unix-user>.json`.
"""

from __future__ import annotations

import argparse
import array
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
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

try:
    import fcntl
except ImportError:  # Windows не поддерживает Linux immutable ioctl.
    fcntl = None  # type: ignore[assignment]

CONFIG_DIR = Path("/etc/hereassistant/runners")
PROVIDER_COMMANDS = {
    "claude_code": "claude",
    "codex": "codex",
    "gemini": "gemini",
}
SSH_REMOTE = re.compile(r"^git@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s]+)$")
SAFE_CONFIG_KEYS = {
    "core.repositoryformatversion",
    "core.filemode",
    "core.bare",
    "core.logallrefupdates",
    "core.ignorecase",
    "core.precomposeunicode",
    "core.worktree",
    "core.autocrlf",
    "core.eol",
    "core.safecrlf",
    "core.symlinks",
    "user.name",
    "user.email",
    "extensions.worktreeconfig",
    "extensions.objectformat",
    "extensions.refstorage",
    "lfs.repositoryformatversion",
}
SAFE_DYNAMIC_CONFIG_KEY = re.compile(
    r"^(?:remote\.[^.]+\.(?:url|fetch)|branch\.[^.]+\.(?:remote|merge))$"
)
FS_IOC_GETFLAGS = 0x80086601
FS_IMMUTABLE_FL = 0x00000010


class RunnerDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerProfile:
    provider: str
    cli_home: Path
    metrics_file: Path


@dataclass(frozen=True)
class RunnerConfig:
    user_id: int
    unix_user: str
    home: Path
    path: str
    accounts: dict[str, RunnerProfile]
    project_roots: tuple[Path, ...]
    git_allowed_hosts: tuple[str, ...]
    git_broker: bool
    git_credential_helper: Path | None
    git_vault_socket: Path | None
    git_database: Path | None
    gitea_oauth_apps: dict[str, str]


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _inside_project_roots(config: RunnerConfig, path: Path) -> bool:
    return any(_inside(path, root) for root in config.project_roots)


def _is_immutable_file(path: Path) -> bool:
    if not sys.platform.startswith("linux") or fcntl is None:
        return False
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        flags = array.array("I", [0])
        fcntl.ioctl(descriptor, FS_IOC_GETFLAGS, flags, True)
        return bool(flags[0] & FS_IMMUTABLE_FL)
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_credential_metadata_lock(config: RunnerConfig, path: Path) -> None:
    if config.git_credential_helper is not None and not _is_immutable_file(path):
        raise RunnerDenied(f"Credentialed Git требует immutable metadata: {path.name}")


def _git_directories(config: RunnerConfig, cwd: Path) -> tuple[Path, Path]:
    matching_roots = [root for root in config.project_roots if _inside(cwd, root)]
    if not matching_roots:
        raise RunnerDenied("Git cwd вне project_roots")
    project_root = max(matching_roots, key=lambda item: len(item.parts))
    dotgit: Path | None = None
    for candidate in (cwd, *cwd.parents):
        if not _inside(candidate, project_root):
            break
        current = candidate / ".git"
        if current.exists():
            dotgit = current
            break
        if candidate == project_root:
            break
    if dotgit is None:
        raise RunnerDenied("Git metadata отсутствует в project root")
    try:
        if dotgit.is_dir():
            git_dir = dotgit.resolve(strict=True)
        elif dotgit.is_file() and dotgit.stat().st_size <= 4096:
            _require_credential_metadata_lock(config, dotgit)
            marker = dotgit.read_text(encoding="utf-8").strip()
            prefix = "gitdir:"
            if not marker.lower().startswith(prefix):
                raise RunnerDenied("Git metadata file повреждён")
            target = Path(marker[len(prefix) :].strip())
            git_dir = (target if target.is_absolute() else dotgit.parent / target).resolve(
                strict=True
            )
        else:
            raise RunnerDenied("Git metadata type запрещён")
        common_marker = git_dir / "commondir"
        if common_marker.is_file() and common_marker.stat().st_size <= 4096:
            _require_credential_metadata_lock(config, common_marker)
            target = Path(common_marker.read_text(encoding="utf-8").strip())
            common_dir = (target if target.is_absolute() else git_dir / target).resolve(strict=True)
        else:
            common_dir = git_dir
    except (OSError, UnicodeError) as error:
        raise RunnerDenied("Git metadata невалидны") from error
    if not _inside_project_roots(config, git_dir) or not _inside_project_roots(config, common_dir):
        raise RunnerDenied("Git metadata выходит за project_roots")
    return git_dir, common_dir


def _config_entries(config: RunnerConfig, path: Path) -> list[tuple[str, str]]:
    resolved = path.resolve(strict=True)
    if not _inside_project_roots(config, resolved) or resolved.stat().st_size > 1_048_576:
        raise RunnerDenied("Git config path/size запрещён")
    executable = shutil.which("git", path=config.path)
    if not executable:
        raise RunnerDenied("Git executable отсутствует")
    try:
        process = subprocess.run(
            [executable, "config", "--file", str(resolved), "--no-includes", "--null", "--list"],
            check=False,
            capture_output=True,
            timeout=5,
            env={
                "HOME": str(config.home),
                "PATH": config.path,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunnerDenied("Git config audit не выполнен") from error
    if process.returncode:
        raise RunnerDenied("Git config невалиден")
    result: list[tuple[str, str]] = []
    for item in process.stdout.split(b"\0"):
        if not item:
            continue
        key, separator, value = item.partition(b"\n")
        if not separator:
            raise RunnerDenied("Git config entry невалиден")
        try:
            result.append((key.decode(errors="strict").lower(), value.decode(errors="strict")))
        except UnicodeError as error:
            raise RunnerDenied("Git config encoding невалидна") from error
    return result


def _validate_remote_url(config: RunnerConfig, value: str) -> None:
    ssh_match = SSH_REMOTE.fullmatch(value)
    if ssh_match:
        host = ssh_match.group("host").lower()
    else:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RunnerDenied("Git remote URL запрещён")
        host = parsed.hostname.lower()
    if host not in config.git_allowed_hosts:
        raise RunnerDenied("Git remote host запрещён")


def audit_git_configuration(config: RunnerConfig, cwd: Path, command: list[str]) -> None:
    if len(command) >= 2 and command[1] == "clone":
        return
    git_dir, common_dir = _git_directories(config, cwd)
    config_paths = [common_dir / "config"]
    worktree_config = git_dir / "config.worktree"
    if worktree_config.exists():
        config_paths.append(worktree_config)
    for config_path in config_paths:
        _require_credential_metadata_lock(config, config_path)
        for key, value in _config_entries(config, config_path):
            if key not in SAFE_CONFIG_KEYS and not SAFE_DYNAMIC_CONFIG_KEY.fullmatch(key):
                raise RunnerDenied(f"Git config key запрещён: {key}")
            if key.startswith("remote.") and key.endswith(".url"):
                _validate_remote_url(config, value)
            elif key == "core.bare" and value.strip().lower() not in ("false", "no", "0"):
                raise RunnerDenied("Bare repository запрещён")
            elif key == "core.worktree":
                worktree = Path(value)
                try:
                    resolved = (
                        worktree if worktree.is_absolute() else common_dir / worktree
                    ).resolve(strict=True)
                except OSError as error:
                    raise RunnerDenied("Git worktree невалиден") from error
                if not _inside_project_roots(config, resolved):
                    raise RunnerDenied("Git worktree выходит за project_roots")


def load_config(unix_user: str, *, config_dir: Path = CONFIG_DIR) -> RunnerConfig:
    path = config_dir / f"{unix_user}.json"
    try:
        config_stat = path.stat()
    except OSError as error:
        raise RunnerDenied("runner config отсутствует") from error
    if config_stat.st_uid != 0 or config_stat.st_mode & 0o022:
        raise RunnerDenied("runner config должен принадлежать root и быть non-writable")
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        user_id = int(raw["user_id"])
        configured_user = str(raw["unix_user"])
        home = Path(raw["home"]).resolve(strict=True)
        account_values: dict[str, dict[str, str]] = dict(raw.get("accounts", {}))
        if not account_values:
            account_values = {
                name: {**value, "provider": name}
                for name, value in dict(raw.get("providers", {})).items()
            }
        root_values = list(raw["project_roots"])
        git_hosts = tuple(str(value).lower() for value in raw.get("git_allowed_hosts", []))
        git_broker = bool(raw.get("git_broker", False))
        helper_value = str(raw.get("git_credential_helper") or "").strip()
        vault_socket_value = str(raw.get("git_vault_socket") or "").strip()
        database_value = str(raw.get("git_database") or "").strip()
        oauth_apps = {
            str(host).strip().lower(): str(client_id).strip()
            for host, client_id in dict(raw.get("gitea_oauth_apps", {})).items()
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise RunnerDenied("runner config повреждён") from error
    if configured_user != unix_user:
        raise RunnerDenied("unix_user не совпадает с именем runner config")
    accounts: dict[str, RunnerProfile] = {}
    for label, value in account_values.items():
        try:
            provider = str(value["provider"])
            cli_home = Path(value["cli_home"]).resolve(strict=True)
            metrics_file = Path(value["metrics_file"])
        except (KeyError, TypeError, OSError) as error:
            raise RunnerDenied("runner provider profile повреждён") from error
        if not metrics_file.is_absolute():
            raise RunnerDenied("runner metrics_file должен быть абсолютным")
        accounts[label] = RunnerProfile(
            provider=provider, cli_home=cli_home, metrics_file=metrics_file
        )
    if any(profile.provider not in PROVIDER_COMMANDS for profile in accounts.values()):
        raise RunnerDenied("runner accounts невалидны")
    if git_broker and accounts:
        raise RunnerDenied("Git broker config не должен содержать provider accounts")
    if not git_broker and not accounts:
        raise RunnerDenied("provider runner accounts пуст")
    if oauth_apps and (
        not git_broker
        or any(
            host not in git_hosts or not client_id or len(client_id) > 512
            for host, client_id in oauth_apps.items()
        )
    ):
        raise RunnerDenied("Gitea OAuth apps невалидны")
    roots = tuple(Path(value).resolve(strict=True) for value in root_values)
    if not roots:
        raise RunnerDenied("runner project_roots пуст")
    credential_helper: Path | None = None
    vault_socket: Path | None = None
    git_database: Path | None = None
    if helper_value:
        if not git_broker:
            raise RunnerDenied("credential helper разрешён только Git broker")
        helper_path = Path(helper_value)
        if not helper_path.is_absolute():
            raise RunnerDenied("Git credential helper должен быть абсолютным")
        try:
            credential_helper = helper_path.resolve(strict=True)
            helper_stat = credential_helper.stat()
        except OSError as error:
            raise RunnerDenied("Git credential helper отсутствует") from error
        if (
            helper_stat.st_uid != 0
            or helper_stat.st_mode & 0o022
            or not helper_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        ):
            raise RunnerDenied("Git credential helper должен быть root-owned executable")
        vault_socket = Path(vault_socket_value)
        if not vault_socket.is_absolute():
            raise RunnerDenied("Git vault socket должен быть абсолютным")
        database_path = Path(database_value)
        if not database_path.is_absolute():
            raise RunnerDenied("Git grant database должна быть абсолютной")
        try:
            git_database = database_path.resolve(strict=True)
            database_stat = git_database.stat()
        except OSError as error:
            raise RunnerDenied("Git grant database отсутствует") from error
        if not git_database.is_file() or database_stat.st_mode & 0o022:
            raise RunnerDenied("Git grant database permissions запрещены")
    elif vault_socket_value:
        raise RunnerDenied("Git vault socket без credential helper запрещён")
    elif database_value:
        raise RunnerDenied("Git grant database без credential helper запрещена")
    return RunnerConfig(
        user_id=user_id,
        unix_user=unix_user,
        home=home,
        path=str(raw.get("path") or "/usr/local/bin:/usr/bin:/bin"),
        accounts=accounts,
        project_roots=roots,
        git_allowed_hosts=git_hosts,
        git_broker=git_broker,
        git_credential_helper=credential_helper,
        git_vault_socket=vault_socket,
        git_database=git_database,
        gitea_oauth_apps=oauth_apps,
    )


def validate_request(
    config: RunnerConfig,
    *,
    user_id: int,
    provider: str,
    account: str,
    cli_home: str,
    cwd: str,
    command: list[str],
) -> tuple[Path, Path]:
    if config.git_broker:
        raise RunnerDenied("Git broker не запускает provider CLI")
    if user_id != config.user_id:
        raise RunnerDenied("Telegram user_id не совпадает с runner")
    profile = config.accounts.get(account)
    if profile is None or profile.provider != provider:
        raise RunnerDenied("account/provider не разрешены runner config")
    try:
        actual_home = Path(cli_home).resolve(strict=True)
        actual_cwd = Path(cwd).resolve(strict=True)
    except OSError as error:
        raise RunnerDenied("provider path недоступен") from error
    if actual_home != profile.cli_home:
        raise RunnerDenied("cli_home не совпадает с runner config")
    if not any(_inside(actual_cwd, root) for root in config.project_roots):
        raise RunnerDenied("cwd вне разрешённых project_roots")
    if not command or command[0] != PROVIDER_COMMANDS[provider]:
        raise RunnerDenied("provider executable не разрешён")
    return actual_home, actual_cwd


def validate_git_request(
    config: RunnerConfig, *, user_id: int, cwd: str, command: list[str]
) -> Path:
    if not config.git_broker:
        raise RunnerDenied("Git разрешён только в отдельном broker config")
    try:
        actual_cwd = Path(cwd).resolve(strict=True)
    except OSError as error:
        raise RunnerDenied("Git cwd недоступен") from error
    if user_id != config.user_id or not any(
        _inside(actual_cwd, root) for root in config.project_roots
    ):
        raise RunnerDenied("Git identity/cwd запрещены")
    if command in (
        ["git", "remote"],
        ["git", "remote", "get-url", "origin"],
        ["git", "remote", "get-url", "github"],
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
    if (
        len(command) == 5
        and command[:3] == ["git", "push", "--dry-run"]
        and command[3] in ("origin", "github")
        and command[4] == "HEAD"
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


def git_environment(config: RunnerConfig, cwd: Path, command: list[str]) -> dict[str, str]:
    """Минимальное окружение Git broker без inherited helpers и app secrets."""
    environment = {
        "HOME": str(config.home),
        "USER": config.unix_user,
        "LOGNAME": config.unix_user,
        "PATH": config.path,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "GIT_CEILING_DIRECTORIES": str(cwd.parent),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GIT_PAGER": "cat",
        "GIT_EDITOR": "/bin/false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": "/dev/null",
        "GIT_CONFIG_KEY_2": "protocol.allow",
        "GIT_CONFIG_VALUE_2": "never",
        "GIT_CONFIG_KEY_3": "protocol.https.allow",
        "GIT_CONFIG_VALUE_3": "always",
        "GIT_CONFIG_KEY_4": "protocol.ssh.allow",
        "GIT_CONFIG_VALUE_4": "always",
        "HEREASSISTANT_GIT_ACCESS": "write" if command[1:2] == ["push"] else "read",
    }
    if config.git_credential_helper is None:
        environment["GIT_CONFIG_COUNT"] = "5"
        return environment
    if config.git_vault_socket is None:
        raise RunnerDenied("Git credential helper требует vault socket")
    environment.update(
        {
            "GIT_CONFIG_COUNT": "7",
            "GIT_CONFIG_KEY_5": "credential.helper",
            "GIT_CONFIG_VALUE_5": str(config.git_credential_helper),
            "GIT_CONFIG_KEY_6": "credential.useHttpPath",
            "GIT_CONFIG_VALUE_6": "true",
            "HEREASSISTANT_GIT_VAULT_SOCKET": str(config.git_vault_socket),
        }
    )
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


def run_git_process(command: list[str], cwd: Path, environment: dict[str, str]) -> int:
    """Запускает Git с private, но group-writable файлами для общей project-group."""
    os.umask(0o007)
    return subprocess.call(command, cwd=cwd, env=environment)


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
    parser.add_argument("--account")
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
            audit_git_configuration(config, cwd, command)
            cli_home = None
        else:
            if not args.provider or not args.cli_home or not args.account:
                raise RunnerDenied("provider/account/cli_home обязательны")
            cli_home, cwd = validate_request(
                config,
                user_id=args.user_id,
                provider=args.provider,
                account=args.account,
                cli_home=args.cli_home,
                cwd=args.cwd,
                command=command,
            )
    except RunnerDenied as error:
        print(f"runner denied: {error}", file=sys.stderr)
        return 77
    if args.git:
        return run_git_process(
            command,
            cwd,
            git_environment(config, cwd, command),
        )
    assert args.provider and cli_home is not None
    return run(
        command,
        cwd,
        provider_environment(config, args.provider, cli_home),
        cli_home,
        config.accounts[args.account].metrics_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
