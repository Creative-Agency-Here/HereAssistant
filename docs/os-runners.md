# Per-user Unix runners

Status: the provider boundary is active for the first production user. A distinct
Git broker UID, credential-helper proxy and fail-closed routing are implemented in
source but are not deployed; fresh installations remain disabled by default.

## Threat boundary

The root-installed wrapper prevents a provider process for one Telegram user from
selecting another user's CLI home or project. Provider and authenticated Git
operations use different Unix identities and different root-owned configs:

```text
ha-ilya      -> provider CLI only, no Git credential helper
ha-ilya-git  -> allowlisted Git only, no provider accounts
```

The wrapper validates Telegram ID, Unix identity, exact mode and resolved cwd.
Bot/API secrets are not copied into either environment.

This protects credentials from other provider processes. It does not make the
trusted HereAssistant core harmless: core still chooses prompts and schedules
runs. A compromised root or core control plane remains outside this boundary.

## Activation prerequisites

`/project clone|pull|push|worktree` use a dedicated Git runner map and strict
command allowlist. Missing `OS_GIT_RUNNER_MAP` or a Git UID equal to the provider
UID fails closed. Telegram attachments remain staged under `downloads/<user_id>`.

## Provisioning skeleton

Examples use placeholders; never commit real Telegram IDs or credentials.

```bash
sudo useradd --create-home --shell /usr/sbin/nologin ha-ilya
sudo useradd --create-home --shell /usr/sbin/nologin ha-pavel
sudo useradd --create-home --shell /usr/sbin/nologin ha-ilya-git
sudo useradd --create-home --shell /usr/sbin/nologin ha-pavel-git

sudo install -d -o ha-ilya -g ha-ilya -m 0700 \
  /home/ha-ilya/cli-homes/claude \
  /home/ha-ilya/cli-homes/codex \
  /home/ha-ilya/projects

sudo install -d -o ha-pavel -g ha-pavel -m 0700 \
  /home/ha-pavel/cli-homes/claude \
  /home/ha-pavel/cli-homes/codex \
  /home/ha-pavel/projects

sudo chmod 0700 /home/ha-ilya-git /home/ha-pavel-git

cd /opt/hereassistant
sudo scripts/install_os_runner.sh
```

Authenticated Git vault rotation requires `systemd-creds` and systemd 250 or
newer. Confirm the host version with `systemd-creds --version` before canary. The
stdin/stdout form and credential-name binding used here were introduced in 250.

For Gitea, register a separate OAuth2 application under user settings and mark it
as a **public client**. Its exact redirect URI is
`https://YOUR_WEBAPP_HOST/api/git/oauth/callback/gitea`. Configure only the public
client ID; HereAssistant intentionally does not accept a Gitea client secret:

```dotenv
GIT_ALLOWED_HOSTS=git.example.com
GITEA_OAUTH_APPS_JSON={"git.example.com":"PUBLIC_CLIENT_ID"}
GIT_OAUTH_STATE_SECRET=GENERATE_A_DEDICATED_RANDOM_48_BYTE_VALUE
```

The state value is stored only as an HMAC; the S256 verifier is deterministically
derived from the state plus the dedicated secret and is never stored in SQLite.
Callbacks are single-use and expire after ten minutes. Automatic refresh-token
rotation is not implemented yet: an expired connection must be reconnected before
private Git operations resume.

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
sudo usermod -aG ha-ilya-core ha-ilya-git
sudo usermod -aG ha-pavel-core here
sudo usermod -aG ha-pavel-core ha-pavel
sudo usermod -aG ha-pavel-core ha-pavel-git
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
Create an equivalent provider file for `ha-pavel` with his Telegram ID and paths.

The Git broker has a separate root-owned config
`/etc/hereassistant/runners/ha-ilya-git.json`:

```json
{
  "user_id": 111111111,
  "unix_user": "ha-ilya-git",
  "home": "/home/ha-ilya-git",
  "path": "/usr/local/bin:/usr/bin:/bin",
  "git_broker": true,
  "accounts": {},
  "project_roots": ["/home/ha-ilya/projects"],
  "git_allowed_hosts": ["github.com", "git.example.com"],
  "git_credential_helper": "/usr/local/libexec/hereassistant-git-credential",
  "git_vault_socket": "/run/hereassistant/git-vault/ha-ilya/broker.sock",
  "git_database": "/opt/hereassistant/bridge.sqlite3",
  "gitea_oauth_apps": {"git.example.com": "PUBLIC_CLIENT_ID"}
}
```

The helper is a credential protocol proxy, not a credential store. It sends only
HTTPS host and repository path to the Unix socket. The vault service must map the
socket peer UID plus that repository to an active owner grant, fetch the opaque
`vault_ref`, and return a short-lived credential. Socket responses are never
logged. Until that root/systemd vault service exists, omit the helper/socket fields:
public Git operations work, authenticated operations fail closed.

The socket directory must not be accessible to the coding runner:

```bash
sudo install -d -o root -g root -m 0755 /run/hereassistant/git-vault
sudo install -d -o root -g ha-ilya-git -m 0750 /run/hereassistant/git-vault/ha-ilya
```

The installed `hereassistant-git-vault@.service` creates `broker.sock` as
`root:ha-ilya-git` with mode `0660`, uses `SO_PEERCRED` to require the configured
Git UID, checks the current SQLite owner/grant and loads an encrypted credential
bundle through systemd `LoadCredentialEncrypted=`. The proxy rejects non-HTTPS
targets, traversal, oversized responses, world-writable sockets and
world-writable socket directories.

The decrypted in-memory bundle uses opaque references already stored in
`git_connections.vault_ref`:

```json
{
  "vault://git/111111111/1/primary": {
    "username": "oauth-user",
    "password": "provider-access-token"
  }
}
```

The root-owned `hereassistant-git-vault-admin` is the only supported writer for
`/etc/hereassistant/git-credentials/ha-ilya-git.json.cred`. It verifies that the
connection belongs to the Telegram user fixed in the runner config, accepts the
credential only as bounded JSON on stdin, invokes `systemd-creds` without secrets
in argv, atomically replaces the encrypted file and uses `systemctl try-restart`
to reload an already active broker. No plaintext credential file is created.

For a controlled callback/canary, the contract is:

```bash
printf '%s' "$OAUTH_JSON_FROM_EPHEMERAL_CALLBACK" | sudo \
  /usr/local/libexec/hereassistant-git-vault-admin \
  --unix-user ha-ilya-git --connection-id CONNECTION_ID put

printf '{}' | sudo /usr/local/libexec/hereassistant-git-vault-admin \
  --unix-user ha-ilya-git --connection-id CONNECTION_ID revoke
```

Do not type a token literally in shell history. The callback process should write
JSON directly to stdin and erase its in-memory exchange result after completion.
After the first encrypted bundle is provisioned, run `systemctl daemon-reload` and
start
`hereassistant-git-vault@ha-ilya-git.service`. The installer copies the unit but
does not start it. The database path is taken exclusively from the root-owned
runner config; it cannot be selected through service or helper argv.

The current bundle is loaded once at service start. Atomic encrypted rotation and
controlled reload are implemented. A Gitea refresh token, when returned, remains
inside this bundle. The `refresh` operation pins host/client ID through the
root-owned runner config, rotates access and refresh tokens internally, and emits
only non-secret `expires_at` metadata to core. Production canary remains pending;
do not place a real credential in the bundle until it passes. The wrapper allowlists
local Git config keys, disables hooks/system/global config and unsafe protocols,
and requires Linux immutable flags on `.git/config`, `config.worktree`, `.git`
pointer and `commondir` whenever a credential helper is configured. Provisioning
must apply `chattr +i` to existing control files after final remote/branch setup;
a missing flag fails closed before Git receives access to the vault socket.

## Sudo boundary

After validating with `visudo -cf`, install a minimal rule that permits Unix user
`here` to invoke only the root-owned wrapper as the two runner users:

```sudoers
Cmnd_Alias HEREASSISTANT_RUNNER = /usr/local/libexec/hereassistant-runner *
here ALL=(ha-ilya,ha-pavel,ha-ilya-git,ha-pavel-git) NOPASSWD: HEREASSISTANT_RUNNER
```

The wildcard does not grant arbitrary commands: arguments are revalidated inside
the root-owned wrapper before a provider executable is started.

When OAuth callbacks are enabled, add the root admin command separately. Its
arguments contain only the mapped Git Unix user, numeric connection ID and
`put|revoke|refresh`; credential JSON crosses stdin only, while refresh has empty
stdin and returns only expiry metadata:

```sudoers
Cmnd_Alias HEREASSISTANT_GIT_VAULT_ADMIN = /usr/local/libexec/hereassistant-git-vault-admin *
here ALL=(root) NOPASSWD: HEREASSISTANT_GIT_VAULT_ADMIN
```

Validate the installed file with `visudo -cf`. The root-owned helper repeats the
owner check against the root-pinned SQLite path; a foreign connection ID fails
closed. Core remains a trusted control plane and must never expose this command
as a generic user-supplied shell surface.

## Application config

Only after account DB rows point to the new CLI homes and all canary checks pass:

```dotenv
OS_RUNNERS_ENABLED=1
OS_RUNNER_MAP=111111111:ha-ilya,222222222:ha-pavel
OS_GIT_RUNNER_MAP=111111111:ha-ilya-git,222222222:ha-pavel-git
OS_RUNNER_EXECUTABLE=/usr/local/libexec/hereassistant-runner
OS_RUNNER_METRICS_DIR=/var/lib/hereassistant/runner-metrics
```

The mode fails closed:

- missing user mapping produces `RUNNER_NOT_CONFIGURED`;
- shared or foreign provider accounts are rejected;
- missing Git mapping and provider/Git UID reuse are rejected;
- provider configs reject Git mode; Git broker configs reject provider mode;
- mismatched CLI homes, cwd roots and executable names exit with code 77;
- Telegram, WebApp and service API secrets are absent from provider env;
- inherited credential helpers and terminal prompts are reset inside Git env;
- RTK command arguments are scrubbed inside the runner;
- only aggregate token savings are exported for `/rtk` and WebApp.

Profiles are routed by the exact database `account.label`, so one user may have
multiple isolated accounts of the same provider without sharing metrics or homes.

## Read-only canary

Before starting a broker, run the repository-provided checker. It validates the
installed root boundary, systemd version/unit, root config, dedicated broker mode,
SQLite integrity/schema and reports current service state. It never starts or
restarts a service:

```bash
cd /opt/hereassistant
sudo scripts/check_git_broker_canary.sh ha-ilya-git
```

After provisioning a synthetic or real encrypted bundle, add
`--require-credential`. The checker decrypts it only into a pipe, validates that
it is JSON and discards the output; it never prints credential fields:

```bash
sudo scripts/check_git_broker_canary.sh --require-credential ha-ilya-git
```

Only after both checks pass should an operator explicitly start the unit and run
the clone/pull/dry-run-push probes. This script is diagnostic and performs no
production rollout.

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
  remote remains unavailable. New source code additionally requires a distinct
  Git broker UID and vault socket; no legacy core credential is copied.
- `ha-pavel` was not created: his Telegram ID and provider credentials are not
  available yet.

## Canary and rollback

1. Back up SQLite and provider profile directories.
2. Provision only `ha-ilya` with a separate canary profile and repository.
3. Verify Claude/Codex login, Git SSH, build/tests, cancellation and RTK metrics.
4. Verify an attempted foreign CLI home and `../`/symlink cwd both return 77.
5. Verify the provider UID cannot invoke the Git mode and the Git UID cannot invoke
   a provider, read CLI homes, `.env`, or another user's vault socket.
6. Verify Git clone/status/pull/worktree/push and attachment reads for each user,
   including negative cross-user and ungranted-repository checks.
7. Rollback is disabling `OS_RUNNERS_ENABLED` and restarting bot/API; no schema
   migration is involved.
