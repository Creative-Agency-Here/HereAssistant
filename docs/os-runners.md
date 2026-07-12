# Per-user Unix runners

Status: code boundary implemented, disabled by default. Do not enable on production
until every checklist item in this document is complete.

## Threat boundary

The root-installed wrapper prevents a provider process for one Telegram user from
selecting another user's CLI home or project. It validates the Telegram ID, Unix
identity, provider executable, exact CLI profile and resolved cwd against a
root-owned config. Bot/API secrets are not copied into the provider environment.

This protects credentials from other provider processes. It does not make the
trusted HereAssistant core harmless: core still chooses prompts and schedules
runs. A compromised root or core control plane remains outside this boundary.

## Current activation blocker

The provider boundary is ready, but production activation is intentionally
blocked until Git operations and attachment staging use the same user boundary.
Today `/project clone|pull|push|worktree` and downloaded attachments are still
owned by Unix user `here`. Enabling private runner-owned repositories before that
broker is implemented would either break those flows or require unsafe broad
filesystem permissions.

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

## Root-owned runner config

`/etc/hereassistant/runners/ha-ilya.json`:

```json
{
  "user_id": 111111111,
  "unix_user": "ha-ilya",
  "home": "/home/ha-ilya",
  "path": "/usr/local/bin:/usr/bin:/bin",
  "providers": {
    "claude_code": {
      "cli_home": "/home/ha-ilya/cli-homes/claude",
      "metrics_file": "/var/lib/hereassistant/runner-metrics/111111111/claude_code.json"
    },
    "codex": {
      "cli_home": "/home/ha-ilya/cli-homes/codex",
      "metrics_file": "/var/lib/hereassistant/runner-metrics/111111111/codex.json"
    }
  },
  "project_roots": ["/home/ha-ilya/projects"]
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

The current root config supports one CLI home per provider for each Unix runner.
Do not enable two accounts of the same provider for one user until account-label
routing is added to the wrapper.

## Canary and rollback

1. Back up SQLite and provider profile directories.
2. Provision only `ha-ilya` with a separate canary profile and repository.
3. Verify Claude/Codex login, Git SSH, build/tests, cancellation and RTK metrics.
4. Verify an attempted foreign CLI home and `../`/symlink cwd both return 77.
5. Keep `OS_RUNNERS_ENABLED=0` for the real accounts until Git/attachment broker
   support lands.
6. Rollback is disabling `OS_RUNNERS_ENABLED` and restarting bot/API; no schema
   migration is involved.
