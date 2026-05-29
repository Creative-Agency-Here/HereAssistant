"""Codex CLI provider."""

from core import config
from .base import CLIProvider


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
        argv += ["-c", f'instructions={config.RU_SYSTEM_INSTRUCTION!r}']
        argv += [full_prompt]

        rc, out, err = await self._exec(argv, cwd)
        if rc != 0:
            raise RuntimeError(f"codex failed (rc={rc}): {err[:2000]}")

        new_session = session_id
        for line in (err + "\n" + out).splitlines():
            if "session" in line.lower() and "id" in line.lower():
                for tok in line.replace(":", " ").replace(",", " ").split():
                    if len(tok) >= 16 and "-" in tok:
                        new_session = tok.strip().strip('"').strip("'")
                        break

        return out.strip(), new_session, {}
