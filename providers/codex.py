"""Codex CLI provider."""

from core import config
from providers.models import ProviderMeta, ProviderResult
from providers.parsers.codex import extract_session_id

from .base import CLIProvider

_extract_session_id = extract_session_id


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
        # инструкция языка через config override
        argv += ["-c", f"instructions={config.RU_SYSTEM_INSTRUCTION!r}"]
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
