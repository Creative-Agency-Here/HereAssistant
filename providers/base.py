"""Базовый CLI-провайдер."""

import asyncio
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from core import config

import logging
log = logging.getLogger("bridge.provider")

# Windows: запускать дочерние CLI без всплывающего консольного окна.
# 0x08000000 = CREATE_NO_WINDOW. На других ОС — 0 (no-op).
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class CLIProvider:
    provider_name: str = ""

    def __init__(self, account: sqlite3.Row):
        self.account = account
        self.cli_home = Path(account["cli_home_path"])
        self.cli_home.mkdir(parents=True, exist_ok=True)

    def env(self) -> dict:
        return {**os.environ}

    async def run(self, prompt: str, cwd: str, session_id: Optional[str],
                  model: Optional[str], attachments: Optional[list[Path]] = None,
                  progress=None) -> tuple[str, Optional[str], dict]:
        """Возвращает (text, new_session_id, meta).
        progress — опциональный async callback (partial_text, event_type) → None,
                   для стриминга промежуточных ответов в Telegram."""
        raise NotImplementedError

    async def _exec(self, argv: list[str], cwd: str,
                    stdin_data: Optional[str] = None) -> tuple[int, str, str]:
        argv = list(argv)
        if os.name == "nt":
            resolved = shutil.which(argv[0])
            if resolved is None:
                raise RuntimeError(
                    f"Не найдена команда '{argv[0]}'. Установлена ли она? "
                    f"Проверь: where {argv[0]}"
                )
            if resolved.lower().endswith((".cmd", ".bat")):
                argv = ["cmd", "/c", resolved, *argv[1:]]
            else:
                argv[0] = resolved

        # diagnostic logging: last arg обычно содержит промпт — покажем первые 200 символов
        prompt_preview = ""
        if argv:
            last = str(argv[-1])
            if len(last) > 30:  # эвристика — длинный аргумент скорее всего промпт
                prompt_preview = last[:200].replace("\n", " ")
        log.info("exec %s in %s | prompt_preview=%r | stdin=%d",
                 argv[0], cwd, prompt_preview, len(stdin_data or ""))

        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=self.env(),
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW,
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
        log.info("exec %s done | rc=%s | out_len=%d | err_len=%d",
                 argv[0], proc.returncode, len(out_text), len(err_text))

        return (proc.returncode or 0, out_text, err_text)
