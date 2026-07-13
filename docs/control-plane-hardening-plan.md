# Control-plane and host hardening plan

**Status:** in progress
**Started:** 2026-07-13
**Scope:** production host, HereAssistant Core, per-user provider/Git runners,
deployment and security audit trail
**Does not change:** provider credentials, account ownership, project membership,
existing repository remotes or privacy policy

## Goal

Reduce the blast radius of a compromised HereAssistant Core or coding agent and
remove avoidable paths from the public network to the trusted control plane.

The target is not described as absolute isolation. On one Linux host, host root
remains able to inspect and modify every local process and file. The practical
target is:

```text
public ingress
  -> minimal bot/API services
  -> narrow policy and runner RPC
  -> per-user provider runner
  -> separate per-user Git runner and credential broker
```

A single user runner must not reach another user's files, processes, credentials,
Git grants or local services. Core must receive only the credentials and
filesystem access required by its own role.

## Safety rules for this rollout

- Every production mutation follows a read-only preflight and a timestamped backup.
- SSH changes are validated with `sshd -t` and a second independent session before
  the first session is closed.
- Firewall changes keep a timed or console-accessible rollback path.
- Existing public proxy ports are not closed until their owner and consumers are
  identified.
- Runner egress starts in observation mode; provider and Git flows are tested before
  enforcement.
- No plaintext provider, OAuth or Telegram credential is printed, copied into the
  repository, passed in argv or written to the execution journal.
- Root never executes an installer directly from the application-writable checkout.
- Each checkpoint records validation and rollback results below.

## Phase 0 — evidence, backup and rollback

- [x] Record git SHA, application version, process tree and active listeners.
- [x] Record effective SSH, firewall, proxy and service configuration.
- [x] Record current Unix users, groups, sudoers and runner mappings without secrets.
- [x] Back up SQLite with the SQLite backup API and run `PRAGMA integrity_check`.
- [x] Back up `.env` without displaying its contents.
- [x] Back up PM2 dump/unit, nginx, SSH, firewall, proxy, runner and vault configs.
- [x] Preserve the current generated WebApp artifact.
- [x] Store checksums and restrictive permissions for the backup set.
- [ ] Prepare and syntax-check a root-owned rollback script.
- [ ] Confirm an independent administrative access path.

**Gate:** no perimeter or service mutation before the backup integrity check and
rollback dry-run pass.

## Phase 1 — urgent host perimeter

- [x] Enable and verify SSH brute-force protection.
- [ ] Restrict SSH by VPN, access gateway or explicit source allowlist where possible.
- [x] Disable password authentication after key-only access is verified.
- [ ] Replace direct root SSH with a named administrator plus audited elevation.
- [ ] Disable unused X11, agent, TCP, tunnel and StreamLocal forwarding. X11,
  agent and tunnel forwarding are disabled; TCP forwarding awaits an operator
  usage decision.
- [x] Set conservative authentication/session limits.
- [ ] Identify consumers of public HTTP/SOCKS proxy listeners.
- [ ] Restrict required proxies to trusted sources; stop and firewall unused ones.
- [ ] Re-scan IPv4 and IPv6 listeners externally and locally.

**Gate:** bot, API, WebApp, Git OAuth callback and both named administrative access
paths pass smoke tests before the original SSH session is closed.

## Phase 2 — contain HereAssistant Core

- [ ] Replace the combined PM2 trust domain with explicit service units or an
  equivalently hardened supervisor configuration.
- [ ] Run bot, WebApp API and background scheduler with separate Unix identities.
- [ ] Give each service a minimal environment; the API must not inherit the bot token
  and the bot must not inherit Git-vault administration capability unless required.
- [ ] Enable `NoNewPrivileges`, a zero capability bounding set, private devices and
  restricted kernel/proc access.
- [ ] Protect system and home paths; add only the required writable paths.
- [ ] Add memory, CPU, process and file-descriptor limits.
- [ ] Add a dedicated AppArmor profile after complain-mode observation.
- [x] Correct supplementary-group drift through a controlled supervisor restart.
- [ ] Verify that service users cannot read another service's environment or files.

**Gate:** Telegram streaming, WebApp, OAuth callback, SQLite migration, attachments,
provider launch and cancellation work through the hardened units.

## Phase 3 — runner network and resource isolation

- [ ] Inventory required provider, Git, DNS and package-registry destinations.
- [ ] Deny cloud metadata and link-local destinations for every runner.
- [ ] Deny localhost and private-network access except explicit runner dependencies.
- [ ] Introduce per-runner egress policy or a mandatory authenticated egress proxy.
- [ ] Add per-user CPU, memory, PID, disk and execution-time quotas.
- [ ] Verify that Ilya's runner cannot reach Pavel's services and vice versa.
- [ ] Verify that provider streaming, login refresh, Git operations, RTK and required
  builds still work.
- [ ] Document unavoidable provider-channel exfiltration risk when an agent can read
  a secret and submit it to its own model endpoint.

**Gate:** negative network probes fail closed while provider and Git canaries remain
green.

## Phase 4 — Git policy and credential boundary

- [ ] Move Git authorization state used by the root broker out of the Core-owned
  application SQLite database.
- [ ] Make repository grants append-only or broker-owned; Core requests changes over
  a narrow authenticated RPC instead of editing trusted grant rows directly.
- [ ] Run vault services as dedicated non-login users where feasible.
- [ ] Add system-call, address-family, device and capability restrictions to vault
  services.
- [ ] Require short-lived, user/project-bound capabilities for clone, pull and push.
- [ ] Add step-up confirmation for first push, remote changes, revoke and sharing.
- [ ] Repeat the token absence audit across processes, journal, SQLite, RTK history,
  Git config and project files.

## Phase 5 — audit, crash and secret hygiene

- [x] Enable command/process auditing for security-relevant services.
- [ ] Send audit and authentication events to a remote append-only destination.
- [ ] Redact OAuth callback query parameters in access logs.
- [x] Keep core dumps disabled for services handling credentials.
- [x] Review and remove stale crash reports only after evidence is recorded.
- [ ] Mount or expose `/proc` with restricted process visibility for untrusted users.
- [ ] Add alerts for repeated SSH failures, root login, sudo runner denial, OAuth
  failure, vault denial and cross-user access attempts.
- [ ] Test backup restore, not only backup creation.

## Phase 6 — immutable deployment and strongest isolation

- [ ] Build a verified artifact from a pinned commit in an unprivileged build context.
- [ ] Install releases into root-owned immutable versioned directories.
- [ ] Keep runtime state outside release directories and switch releases atomically.
- [ ] Use a fixed root-owned deploy/rollback executable; never execute root scripts
  from the Core-owned checkout.
- [ ] Sign or checksum release manifests and preserve deployment provenance.
- [ ] Move private client runners into per-user VM or microVM boundaries.
- [ ] For clients requiring independent host-root domains, use separate hosts or
  separate infrastructure accounts.

## Acceptance criteria

- Public SSH and proxy exposure is explicitly justified and source-restricted.
- Password and direct root SSH access are disabled after recovery access is proven.
- Brute-force protection and security auditing are active.
- Core services use distinct identities, minimal credentials and hardened units.
- User runners cannot access another user, localhost control-plane services, cloud
  metadata or unapproved private networks.
- A compromised Core cannot create a valid Git grant by directly modifying its own
  application database.
- Provider and Git credentials remain absent from logs, argv, SQLite and workspaces.
- Backups and rollback are tested, not merely documented.
- All existing isolation, privacy, provider, WebApp and Git tests remain green.
- Remaining single-host root trust is stated explicitly in security documentation.

## Execution journal

### 2026-07-13 — baseline audit

- [x] Confirmed that per-user provider and Git runner boundaries are installed.
- [x] Confirmed that direct cross-user filesystem checks fail at the runner boundary.
- [x] Confirmed that Core remains a trusted scheduler capable of invoking each user
  runner and that application SQLite remains inside the Core trust domain.
- [x] Confirmed unrestricted runner access to the public network and selected local
  control-plane endpoints.
- [x] Confirmed that HereAssistant processes have no effective capabilities, but do
  not use seccomp or `NoNewPrivileges` and have no dedicated AppArmor profile.
- [x] Confirmed unrestricted process visibility through the current `/proc` mount.
- [x] Confirmed active public SSH and proxy listeners and inactive SSH brute-force
  protection at audit time.
- [x] Confirmed that service account passwords are locked; direct root key login is
  still enabled.
- [x] Confirmed that full command-level audit is not active.
- [x] Found a stale application crash report readable inside the Core trust domain.
- [x] Found supplementary-group drift between the `here` account and running bot/API
  processes; this requires a controlled full supervisor restart, not a simple child
  process restart.
- [x] Phase 0 production backup set created and verified.
- [x] Root-owned rollback command prepared and verified.

### 2026-07-13 — Phase 0 backup checkpoint

- [x] Created a timestamped snapshot under the root-only production backup root.
- [x] Used Python's SQLite online backup API because the host intentionally has no
  standalone `sqlite3` executable.
- [x] Verified `PRAGMA integrity_check=ok` for both source and backup databases.
- [x] Verified snapshot checksums and required `.env`, SQLite and generated WebApp
  artifacts; the completed snapshot contains 80 files and is `root:root 0700`.
- [x] Verified a key-only named SSH login for the primary administrator.
- [ ] Named login is not yet an independent administrative path: its sudo policy is
  deliberately limited to provider authentication. Direct root SSH remains required
  until a separate administrator elevation design is approved.
- [x] Confirmed that SSH brute-force protection is not installed on the host.
- [x] Confirmed that neither Tailscale nor an active Cloudflare SSH service currently
  provides a recovery path on this host.
- [x] Confirmed that both public proxy services require a usage decision before
  firewall restriction; the SOCKS service has substantial recent journal activity.

### 2026-07-13 — first production hardening checkpoint

- [x] Installed and enabled SSH brute-force protection. The jail uses the systemd
  backend, aggressive SSH matching, five attempts per ten minutes and increasing
  bans capped at one day. It banned active sources immediately, while fresh root
  and named-administrator key sessions remained available.
- [x] Installed `/etc/ssh/sshd_config.d/00-hereassistant-hardening.conf`. The `00`
  prefix is required because OpenSSH keeps the first obtained value and the host's
  cloud-init file previously overrode the later hardening file.
- [x] Effective SSH now rejects password, keyboard-interactive and empty-password
  authentication; X11, agent and tunnel forwarding are disabled; login grace is
  30 seconds and authentication attempts are limited to three.
- [x] Kept direct root key login and TCP forwarding temporarily. The named account
  has only narrowly scoped provider-login sudo rules, so it cannot yet replace the
  root administrative recovery path.
- [x] Installed `/usr/local/sbin/hereassistant-control-plane-rollback` as
  `root:root 0700`; its non-mutating check verifies the snapshot checksums, SSH and
  brute-force configuration. The SSH change additionally used a timed systemd
  rollback that was cancelled only after two fresh key-only sessions succeeded.
- [x] Converted the previously detached PM2 daemon into an enabled and active
  `pm2-here.service`. Bot and API received the Ilya, Pavel and metrics supplementary
  groups after a full daemon restart; local and public API health stayed green.
- [x] Installed `/etc/systemd/system/pm2-here.service.d/10-hereassistant-hardening.conf`.
  Core dumps, private device/IPC/tmp access, kernel mutation/log access, realtime,
  SUID/SGID creation and foreign process visibility are restricted. The real
  Claude runner boundary was successfully invoked from the PM2 mount namespace.
- [x] Did not set `NoNewPrivileges`: the current Core-to-runner contract deliberately
  uses sudo to enter per-user Unix identities. It can be enabled only after runner
  invocation moves to a narrow service RPC.
- [x] PM2's systemd security exposure score improved to `6.5 MEDIUM`. Remaining
  exposure is primarily the writable application/runtime layout, shared Core
  identity and required sudo/network access.
- [x] Installed and enabled auditd with 11 focused integrity/execution watches in
  `/etc/audit/rules.d/hereassistant.rules`; the first checkpoint reported zero lost
  audit events. Logs are local and are not yet tamper-resistant against host root.
- [x] Removed the stale application crash report only after its root-only backup was
  checksum-verified. Active bot/API processes now have hard and soft core limits of
  zero.
- [x] Public WebApp and `/api/health` returned HTTP 200 after the changes, PM2 had no
  restart loop and SQLite `PRAGMA quick_check` returned `ok`.
- [x] Set the PM2 service umask to `0007`, preserving the per-user collaboration
  groups while preventing newly created runtime files from being world-readable.
- [x] Added an aiohttp access logger that records only the request path and never
  the query string, preventing OAuth `code`/`state`, WebApp keys and `tma` data from
  entering new API access-log records.
- [x] Added a regression test for access-log redaction and passed the complete
  quality gate: Ruff, Pyright, repository hygiene and 466 tests.

### Open decisions after the first checkpoint

- [ ] Decide whether the public SOCKS listener can be disabled. The audit found no
  successful pass in the sampled day, but more than ten thousand blocked attempts.
- [ ] Define trusted source ranges or an access gateway for the actively used HTTP
  proxy before restricting it.
- [ ] Choose the named administrator elevation model before disabling direct root
  SSH: hardware-backed key plus sudo authentication is preferred over unrestricted
  passwordless sudo.
- [ ] Provide an external append-only syslog/audit destination if remote tamper-
  resistant audit is required.
- [ ] Inventory provider/Git/package endpoints before runner egress enforcement.

Further entries must record only non-secret evidence: timestamp, changed files or
units, validation result, rollback result and remaining risk.
