# RTK canary plan

Status: preparation only. RTK is not installed and no agent configuration has been changed.

## Pinned artifact

The reviewed canary version is `v0.43.0`, released 2026-06-28. Do not replace it with `latest` during rollout.

```text
asset:  rtk-x86_64-unknown-linux-musl.tar.gz
sha256: ff8a1e7766496e175291a85aeca1dc97c9ff6df33e51e5893d1fbc78fea2a609
source: https://github.com/rtk-ai/rtk/releases/tag/v0.43.0
```

Reconfirm `uname -m` and the checksum from GitHub release metadata immediately before installation.

## Preconditions

- Account/project isolation is deployed and verified.
- No legacy account remains an unassigned implicit shared account.
- A separate disabled-by-default Claude canary account has its own `cli_home_path` and owner.
- The canary uses a disposable repository without client data.
- Database and configuration backups exist outside the profile directory with mode `0700`.

## Installation rehearsal

1. Download the pinned archive to a temporary directory.
2. Require an exact `sha256sum` match and inspect the archive file list.
3. Extract as an unprivileged user; run `rtk --version` and `rtk gain` from that path.
4. Only after rehearsal, install that exact binary as `/usr/local/bin/rtk`.
5. Preserve the existing process `PATH`; do not replace it with a shortened value.
6. Set `RTK_TELEMETRY_DISABLED=1` for the canary and later in PM2.

## Canary initialization

1. Record hashes and permissions of the canary configuration.
2. Back it up without printing credential contents.
3. Run `rtk init --show` first and record intended edits.
4. Verify experimentally whether this RTK version honours `CLAUDE_CONFIG_DIR`; public RTK documentation does not guarantee HereAssistant's custom layout.
5. Run `rtk init -g` only for the canary. Do not use a bulk loop.
6. Diff redacted configuration, filenames, and modes before/after.
7. Keep Claude based on `CLAUDE_CONFIG_DIR`. Do not change `HOME` merely to isolate RTK stats: it also changes SSH, Git, shell, and `~` behaviour.

## Acceptance checks

Run `rtk --version`, `rtk gain`, `rtk git status`, and `rtk ls .`, then invoke Claude through the normal HereAssistant subprocess path. Verify:

- supported Bash commands are rewritten;
- built-in `Read`, `Grep`, and `Glob` remain normal;
- Git identity and SSH test authentication are correct;
- failures preserve non-zero exit codes and recoverable raw output;
- build, lint, type checks, and tests have unchanged results;
- `rtk gain --history` contains only canary activity;
- no credentials, source paths, or command arguments appear in telemetry or logs.

## Rollback and rollout

Stop using the canary, restore its exact pre-init configuration and modes, verify Claude without the hook, and remove `/usr/local/bin/rtk` only if no verified profile uses it. Remove the PM2 override only when RTK is fully removed.

Promote one profile at a time: canary Claude, one owned Claude profile, owned Codex, then Gemini after Gemini CLI is installed. Codex integration is instruction-based rather than the same Claude hook mechanism, so it needs its own acceptance run. Stop on any Git/SSH, permission, exit-code, or output-integrity regression.
