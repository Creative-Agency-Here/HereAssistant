# Per-user Unix runners

Status: provider, Git and attachment boundaries implemented. The first production
user was activated through a canary on 2026-07-12; fresh installations remain
disabled by default. Additional users still require the full provisioning list.

## Threat boundary

The root-installed wrapper prevents a provider process for one Telegram user from
selecting another user's CLI home or project. It validates the Telegram ID, Unix
identity, provider executable, exact CLI profile and resolved cwd against a
root-owned config. Bot/API secrets are not copied into the provider environment.

This protects credentials from other provider processes. It does not make the
trusted HereAssistant core harmless: core still chooses prompts and schedules
runs. A compromised root or core control plane remains outside this boundary.

## Activation prerequisites

`/project clone|pull|push|worktree` now use the same runner with a strict Git
command allowlist. Telegram attachments are staged under `downloads/<user_id>`.
Production still needs per-user Unix groups and ownership before activation.

## Provisioning skeleton

Examples use placeholders; never commit real Telegram IDs or credentials.

```bash
sudo useradd --create-home --shell /usr/sbin/nologin ha-ilya
sudo useradd --create-home --shell /usr/sbin/nologin ha-pavel

sudo install -d -o ha-ilya -g ha-ilya -m 0700 \
  /home/ha-ilya/cli-homes/claude \
  /home/ha-ilya/cli-homes/codex \
  /home/ha-ilya/projects

sudo install -d -o ha-pavel -g ha-pavel -m 0700 \
  /home/ha-pavel/cli-homes/claude \
  /home/ha-pavel/cli-homes/codex \
  /home/ha-pavel/projects

cd /opt/hereassistant
sudo scripts/install_os_runner.sh
```

Create a metrics-only group. It grants `here` access only to aggregate JSON, not
to CLI credentials or RTK command history.

```bash
sudo groupadd --force hereassistant-metrics
sudo usermod -aG hereassistant-metrics here
sudo usermod -aG hereassistant-metrics ha-ilya
sudo usermod -aG hereassistant-metrics ha-pavel
sudo install -d -o ha-ilya -g hereassistant-metrics -m 2750 \
  /var/lib/hereassistant/runner-metrics/USER_ID_ILYA
sudo install -d -o ha-pavel -g hereassistant-metrics -m 2750 \
  /var/lib/hereassistant/runner-metrics/USER_ID_PAVEL
```

Create separate collaboration groups for repositories and attachment staging;
never put both runners in one shared group:

```bash
sudo groupadd --force ha-ilya-core
sudo groupadd --force ha-pavel-core
sudo usermod -aG ha-ilya-core here
sudo usermod -aG ha-ilya-core ha-ilya
sudo usermod -aG ha-pavel-core here
sudo usermod -aG ha-pavel-core ha-pavel
sudo chown -R ha-ilya:ha-ilya-core /home/ha-ilya/projects
sudo chown -R ha-pavel:ha-pavel-core /home/ha-pavel/projects
sudo chmod -R 2770 /home/ha-ilya/projects /home/ha-pavel/projects
sudo install -d -o here -g ha-ilya-core -m 2750 /opt/hereassistant/.runtime/downloads/USER_ID_ILYA
sudo install -d -o here -g ha-pavel-core -m 2750 /opt/hereassistant/.runtime/downloads/USER_ID_PAVEL
```

## Root-owned runner config

`/etc/hereassistant/runners/ha-ilya.json`:

```json
{
  "user_id": 111111111,
  "unix_user": "ha-ilya",
  "home": "/home/ha-ilya",
  "path": "/usr/local/bin:/usr/bin:/bin",
  "accounts": {
    "claude-main": {
      "provider": "claude_code",
      "cli_home": "/home/ha-ilya/cli-homes/claude",
      "metrics_file": "/var/lib/hereassistant/runner-metrics/111111111/claude-main.json"
    },
    "codex-main": {
      "provider": "codex",
      "cli_home": "/home/ha-ilya/cli-homes/codex",
      "metrics_file": "/var/lib/hereassistant/runner-metrics/111111111/codex-main.json"
    }
  },
  "project_roots": ["/home/ha-ilya/projects"],
  "git_allowed_hosts": ["github.com", "git.example.com"]
}
```

Apply `root:root 0644`. The wrapper rejects configs writable by group or others.
Create an equivalent file for `ha-pavel` with his Telegram ID and paths.

## Sudo boundary

After validating with `visudo -cf`, install a minimal rule that permits Unix user
`here` to invoke only the root-owned wrapper as the two runner users:

```sudoers
Cmnd_Alias HEREASSISTANT_RUNNER = /usr/local/libexec/hereassistant-runner *
here ALL=(ha-ilya,ha-pavel) NOPASSWD: HEREASSISTANT_RUNNER
```

The wildcard does not grant arbitrary commands: arguments are revalidated inside
the root-owned wrapper before a provider executable is started.

## Application config

Only after account DB rows point to the new CLI homes and all canary checks pass:

```dotenv
OS_RUNNERS_ENABLED=1
OS_RUNNER_MAP=111111111:ha-ilya,222222222:ha-pavel
OS_RUNNER_EXECUTABLE=/usr/local/libexec/hereassistant-runner
OS_RUNNER_METRICS_DIR=/var/lib/hereassistant/runner-metrics
```

The mode fails closed:

- missing user mapping produces `RUNNER_NOT_CONFIGURED`;
- shared or foreign provider accounts are rejected;
- mismatched CLI homes, cwd roots and executable names exit with code 77;
- Telegram, WebApp and service API secrets are absent from provider env;
- RTK command arguments are scrubbed inside the runner;
- only aggregate token savings are exported for `/rtk` and WebApp.

Profiles are routed by the exact database `account.label`, so one user may have
multiple isolated accounts of the same provider without sharing metrics or homes.

## First production activation (2026-07-12)

- A dedicated `ha-ilya` Unix user owns the migrated Claude profile and workspace.
- HereAssistant core runs as `here` and reaches the profile only through the
  root-owned wrapper; direct credential reads by `ha-ilya` outside its profile
  and reads of the application `.env` were denied in canary checks.
- Foreign user ID, foreign cwd, arbitrary Git command and parent-repository
  discovery checks fail closed.
- Claude streaming, session creation, Git status/pull, attachment staging and
  sanitized RTK aggregate export passed through the production boundary.
- Bot/API remained online and SQLite integrity stayed `ok` after activation.
- The public GitHub remote is the branch pull upstream. Push to the private Gitea
  remote remains unavailable inside the runner until a dedicated per-user HTTPS
  credential/deploy token is provisioned; no legacy core credential was copied.
- `ha-pavel` was not created: his Telegram ID and provider credentials are not
  available yet.

## Canary and rollback

1. Back up SQLite and provider profile directories.
2. Provision only `ha-ilya` with a separate canary profile and repository.
3. Verify Claude/Codex login, Git SSH, build/tests, cancellation and RTK metrics.
4. Verify an attempted foreign CLI home and `../`/symlink cwd both return 77.
5. Verify Git clone/status/pull/worktree/push and attachment reads for each user,
   including negative cross-user checks.
6. Rollback is disabling `OS_RUNNERS_ENABLED` and restarting bot/API; no schema
   migration is involved.
