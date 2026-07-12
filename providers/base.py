"""Базовый CLI-провайдер."""

import asyncio
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from core import config, rtk
from providers.models import ProgressCallback, ProviderMeta
from providers.os_runner import ProcessBoundary

log = logging.getLogger("bridge.provider")

# Windows: запускать дочерние CLI без всплывающего консольного окна.
# 0x08000000 = CREATE_NO_WINDOW. На других ОС — 0 (no-op).
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class CLIProvider:
    provider_name: str = ""

    def __init__(self, account: sqlite3.Row, user_id: int | None = None):
        self.account = account
        self.user_id = user_id
        self.boundary = ProcessBoundary(account, user_id)
        self.cli_home = Path(account["cli_home_path"])
        if not self.boundary.enabled:
            self.cli_home.mkdir(parents=True, exist_ok=True)

    def env(self) -> dict[str, str]:
        return {**os.environ, **rtk.runtime_env(self.cli_home)}

    def cleanup_runtime(self) -> None:
        if not self.boundary.enabled:
            rtk.sanitize_runtime(self.cli_home)

    async def _spawn(
        self,
        argv: list[str],
        cwd: str,
        *,
        stdin: int | None,
        limit: int | None = None,
    ) -> asyncio.subprocess.Process:
        provider = self.provider_name or str(self.account["provider"])
        prepared = self.boundary.prepare(argv, cwd, provider)
        environment = prepared.env if self.boundary.enabled else self.env()
        if limit is None:
            return await asyncio.create_subprocess_exec(
                *prepared.argv,
                cwd=prepared.cwd,
                env=environment,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=NO_WINDOW,
            )
        return await asyncio.create_subprocess_exec(
            *prepared.argv,
            cwd=prepared.cwd,
            env=environment,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW,
            limit=limit,
        )

    async def run(
        self,
        prompt: str,
        cwd: str,
        session_id: Optional[str],
        model: Optional[str],
        attachments: Optional[list[Path]] = None,
        progress: ProgressCallback | None = None,
    ) -> tuple[str, Optional[str], ProviderMeta]:
        """Возвращает (text, new_session_id, meta).
        progress — опциональный async callback (partial_text, event_type) → None,
                   для стриминга промежуточных ответов в Telegram."""
        raise NotImplementedError

    async def _exec(
        self, argv: list[str], cwd: str, stdin_data: Optional[str] = None
    ) -> tuple[int, str, str]:
        argv = list(argv)
        if os.name == "nt":
            resolved = shutil.which(argv[0])
            if resolved is None:
                raise RuntimeError(
                    f"Не найдена команда '{argv[0]}'. Установлена ли она? Проверь: where {argv[0]}"
                )
            if resolved.lower().endswith((".cmd", ".bat")):
                argv = ["cmd", "/c", resolved, *argv[1:]]
            else:
                argv[0] = resolved

        log.info(
            "exec %s | argc=%d | stdin_len=%d",
            argv[0],
            len(argv),
            len(stdin_data or ""),
        )

        proc = await self._spawn(
            argv,
            cwd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_data.encode("utf-8") if stdin_data is not None else None),
                timeout=config.CLI_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"CLI timeout after {config.CLI_TIMEOUT}s")

        out_text = stdout.decode(errors="replace")
        err_text = stderr.decode(errors="replace")
        log.info(
            "exec %s done | rc=%s | out_len=%d | err_len=%d",
            argv[0],
            proc.returncode,
            len(out_text),
            len(err_text),
        )

        return (proc.returncode or 0, out_text, err_text)
