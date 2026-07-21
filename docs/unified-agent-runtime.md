# Unified Claude and Codex runtime

## Goal

HereAssistant exposes one logical conversation while Claude Code and Codex remain
interchangeable engines. Native session IDs are never mixed. Cross-provider context is built
from explicitly allowed conversation history, repository rules, and owner/project-scoped
memory.

## Project configuration

```yaml
mode: local
storage:
  save_history: true
  save_messages: true
  save_file_changes: true
agent:
  profile: unified
  memory:
    enabled: true
    max_items: 6
    max_context_chars: 12000
```

Message storage enables transcript handoff between providers. Memory is a separate local
opt-in and never weakens CRM privacy gates.

## Shared memory

The source of truth is `<project>/.hereassistant/memory/*.md`. Keep this directory excluded
from Git. `MEMORY.md` is the compact index; other files contain topic notes. HereAssistant
injects only the index and query-relevant notes, enforces context limits, ignores symlinks, and
skips files that look like they contain secrets.

Import existing Claude memory:

```bash
python3 scripts/import_claude_memory.py \
  --user-id TELEGRAM_USER_ID \
  --project-id HEREASSISTANT_PROJECT_ID \
  --source-dir /path/to/claude/project/memory \
  --copy-to-shared
```

Link native Claude memory to the shared directory:

```bash
python3 scripts/link_claude_memory.py \
  --user-id TELEGRAM_USER_ID \
  --project-id HEREASSISTANT_PROJECT_ID \
  --claude-home /path/to/claude/profile
```

The link command refuses to replace a non-empty native memory directory. Import it first;
existing data is never deleted automatically.

## Lifecycle hooks

Project hooks stay with the repository because only the repository knows its Git and deploy
rules. Codex loads trusted `.codex/hooks.json`; Claude uses the matching
`.claude/hooks.template.json` merged into gitignored `.claude/settings.local.json`. Shared
account pinning, secret scanning, CRM session tasks, Git ownership, session sync, and handoff
implementations remain in the target repository's `scripts/hooks/` directory.

For the HereAgency Site/Service repositories:

```bash
pnpm hooks:status
pnpm hooks:install
```

Codex still requires a one-time `/hooks` review and trust action in every server clone. The
gateway must not bypass this consent by writing trust state automatically.

## Provider switching

Select another profile through `/accounts`. HereAssistant resets only the native provider
session ID. With `storage.save_messages: true`, the new engine receives allowed conversation
history; both engines receive the same memory index and relevant notes. Private projects
without explicit storage flags intentionally have no cross-provider transcript handoff.
