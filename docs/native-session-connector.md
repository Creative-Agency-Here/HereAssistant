# Native CLI sessions → HereAssistant → HereCRM

The native connector records direct Claude Code, Codex, Qwen Code and Gemini CLI sessions
through the same privacy gates and reliable outbox as HereAssistant-launched conversations.
It does not copy provider credentials, replace project lifecycle hooks, or enable global
tracking.

## Privacy boundary

- No nearest `.hereassistant/project.yml` means `private`; no event is queued.
- A parent policy applies to its descendants. A nested `mode: private` file overrides it.
- `local` never reaches HereCRM.
- `crm` requires `sync.enabled: true` and a CRM project or task ID.
- Transcript content is not read unless `send_prompts` or `send_messages` is explicitly true.
- A transcript path is accepted only inside that provider's normal auth/session home.

## Configure the connector

Set the existing scoped HereCRM transport values in the local HereAssistant `.env`:

```dotenv
HERECRM_SYNC_URL=https://crm-api.example.com/api/v1
HERECRM_SYNC_TOKEN=has_COPY_ONCE
HEREASSISTANT_NATIVE_USER_ID=123456789
HERECRM_SYNC_ORIGIN=employee-laptop
```

The token needs `sessions:write`; keep `sessions:read` if this installation also reads the
owner-only CRM activity view. Never send the token in chat or commit `.env`.

Open the unified manager:

```bash
.venv/bin/python manage.py
```

Choose **Settings → AI sessions → HereCRM**. The screen shows connector, identity, outbox
and all four CLI states. It can install/update hooks, configure a project folder, or uninstall
only the hooks owned by HereAssistant.

The non-interactive equivalents are:

```bash
.venv/bin/python scripts/native_connector.py status
.venv/bin/python scripts/native_connector.py install --clients all
.venv/bin/python scripts/native_connector.py uninstall --clients all
```

Installation preserves unrelated hooks, creates private backups under
`~/.hereassistant/hook-backups/`, and is idempotent. The hook command points at the current
HereAssistant checkout and Python interpreter; rerun install after moving the checkout or
recreating its virtual environment.

## Configure tracked folders

Use the manager to create or safely update `<project>/.hereassistant/project.yml`. Metadata-only
CRM visibility is the recommended starting point:

```yaml
name: "Example project"
mode: crm
crm_project_id: "CRM_PROJECT_UUID"
sync:
  enabled: true
  send_prompts: false
  send_messages: false
  send_diffs: false
  send_commits: false
  send_deploys: false
  send_artifacts: false
```

This file contains policy, not credentials, and may be shared with the team if the CRM IDs are
appropriate for every contributor. Use an uncommitted nested `mode: private` policy for an
excluded subtree.

## Employee rollout

Each employee installs HereAssistant and the hooks on their own computer. Provider logins stay
local. Give every installation its own scoped token, native user ID and origin name; do not
share one employee's `.env`.

After installation:

1. Restart open CLI sessions. Review Codex hooks with `/hooks`; approve project trust where a
   client requires it.
2. Start a short session from a folder explicitly configured as `crm`.
3. Run `native_connector.py status`; the outbox should return to zero after delivery.
4. Confirm the session owner, provider, model, terminal surface and project in HereCRM.
5. Repeat from an unconfigured folder and verify that no CRM session appears.

If an older direct-sync hook already exists, remove it with its original installer after this
connector is verified. HereAssistant intentionally never deletes hooks it does not own.
