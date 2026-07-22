"""Codex CLI provider."""

from core import config
from providers.models import ProviderMeta, ProviderResult
from providers.parsers.codex import extract_session_id

from .base import CLIProvider

_extract_session_id = extract_session_id


def permission_args(mode: str) -> list[str]:
    """Безопасные режимы для неинтерактивного codex exec."""
    if mode == "read-only":
        return ["--sandbox", "read-only", "-c", "approval_policy=never"]
    if mode == "workspace":
        return ["--sandbox", "workspace-write", "-c", "approval_policy=never"]
    return []


class CodexProvider(CLIProvider):
    provider_name = "codex"

    def env(self) -> dict:
        e = super().env()
        e["CODEX_HOME"] = str(self.cli_home)
        return e

    async def run(self, prompt, cwd, session_id, model, attachments=None, progress=None):
        # Codex пока не стримит — progress игнорируется
        full_prompt = prompt or ""
        if attachments:
            paths = "\n".join(f"- {p}" for p in attachments)
            full_prompt += f"\n\n[Прикреплённые файлы]\n{paths}\n"

        if session_id:
            argv = ["codex", "exec", "resume", session_id, "--skip-git-repo-check"]
        else:
            argv = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            argv += ["-c", f"model={model}"]
        argv += permission_args(self.permission_mode)
        # инструкция языка через config override
        codex_instructions = (
            config.RU_SYSTEM_INSTRUCTION
            + "\n\nShell-команды начинай с rtk для сжатия вывода: "
            "rtk git status, rtk ls, rtk grep ..., rtk pytest ... "
            "Для составных команд: rtk sh -c '...'. "
            "Это экономит контекстные токены."
        )
        argv += ["-c", f"instructions={codex_instructions!r}"]
        argv += [full_prompt]

        rc, out, err = await self._exec(argv, cwd)
        if rc != 0:
            raise RuntimeError(f"codex failed (rc={rc}): {err[:2000]}")

        result = ProviderResult(
            text=out.strip(),
            session_id=_extract_session_id(out, err, session_id),
            meta=ProviderMeta(),
        )
        return result.as_tuple()
