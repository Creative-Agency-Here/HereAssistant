# Remote control (`/rc`)

Russian version: [remote-control.ru.md](remote-control.ru.md)

`/rc` lets you publish the live terminal-chat session you are currently running
(`chat.py`) so it can be observed and, if the project's privacy policy allows it,
driven from a browser instead of typing into the terminal. It is off by default
for every project and stays off unless you opt in explicitly.

## Current implementation status

Read this before relying on `/rc` for anything:

- **What works today, fully local:** `/rc`, `/rc status`, `/rc off`, `/rc stop`,
  the privacy gates, and the single-writer queue that arbitrates local vs.
  remote input all run inside `chat_remote_control.py` /
  `core/remote_control/` and are covered by tests
  (`tests/test_chat_remote_control.py`, `tests/core/test_remote_control_*.py`).
  Publishing writes a row to the local `rc_publications` table and nothing
  more — no network call is made just to run `/rc`.
- **What is implemented but not wired into the bundled terminal client:**
  `core/remote_control/control_plane_client.py` (`ControlPlaneClient`,
  `WakeupListener`) is a complete, tested HTTPS+WSS client for a control-plane
  backend, and `core/remote_control/credential_store.py` is a complete device
  credential store (macOS Keychain / file `0600`). As shipped, `chat.py`
  constructs `RemoteControlCoordinator` without a `control_client`, so the
  coordinator's network loop never starts, and there is no command in this
  repository that provisions or pairs a device credential yet. Setting
  `RC_CONTROL_PLANE_URL` alone does not connect a running terminal session —
  it configures the client that a control-plane integration would inject.
- **What the browser side already exposes:** `webapp/api/routes/remote_control.py`
  is a real, tested (`tests/webapp/test_remote_control_routes.py`) same-origin
  proxy that a signed-in WebApp browser session can use to list publications
  and post commands, and `webapp/front/components/remote-control/` plus
  `webapp/front/composables/useRemoteControl.ts` implement the corresponding
  UI. This side only works against a control-plane backend that implements the
  matching contract; HereAssistant does not ship that backend.
- **Command types the device would execute if wired up:** only `prompt` is
  currently pulled and executed by the terminal client's reconcile loop
  (`chat_remote_control.py`'s `_ingest_remote_command`). `core/remote_control/git_actions.py`
  implements `git_preflight` / `git_commit` / `git_push` against the exact
  contract described below and is covered by its own tests
  (`tests/core/test_remote_control_git.py`), but nothing currently calls it
  from the command-ingestion path — treat it as a specified, tested building
  block, not as an already-live remote action.
- **Outbound event streaming:** `core/remote_control/events.py` defines five
  event types (`rc.progress`, `rc.tool_call`, `rc.approval_required`,
  `rc.diff_summary`, `rc.command_status`), each behind its own privacy gate.
  Only `emit_command_status` is actually called today (on queue/run/finish
  transitions); the other four are implemented and gated but not yet invoked.
  Every event is written to a local durable outbox (`rc_event_outbox`) first;
  nothing in the current codebase drains that outbox to a server, so it
  accumulates locally until that piece is wired up.

In short: `/rc` is the safety-and-arbitration core of a remote-control feature —
privacy gates, idempotent command receipts, a durable local outbox, a
credential store, and a durable HTTPS+notify-WSS client — built and tested as
independent pieces, with the terminal entrypoint not yet assembling them into a
live connection. The rest of this document describes the contract each piece
implements, since that contract is what a control-plane integration is
expected to speak.

## Commands

| Command | Aliases | Effect |
|---|---|---|
| `/rc` | `/rc on`, `/rc publish` | Publish the current session (subject to policy). No-op if already published. |
| `/rc status` | `/rc st` | Show publication state, privacy mode, device, and the local execution queue. |
| `/rc stop` | — | Cancel the run currently in progress. Does not touch the queue. |
| `/rc off` | `/rc close` | Unpublish immediately and clear the queue. |

Switching provider, account, model, working directory, or the current session
(`/model`, `/account`, `/cwd`, `/new`, `/resume`, `/user`) is blocked while a
publication is active — you have to `/rc off` first
(`CommandRouter._rc_blocked` in `chat_commands.py`).

## Local vs. remote input: one writer, one queue

`RemoteControlCoordinator` is the session's single writer to the provider CLI:

- exactly one provider run at a time, serialized by an `asyncio.Lock`;
- a run currently in progress is **never pre-empted** — new input, local or
  remote, is appended to an in-memory FIFO queue;
- ordering is by the moment input was accepted; ties are broken in favor of
  local input (`_SOURCE_RANK = {"local": 0, "remote": 1}`);
- a remote prompt never changes provider, account, model, working directory,
  or permission mode — it is only a prompt string going through the same
  `_run_prompt` path as local input.

`/rc status` prints the running item's source and a preview of every queued
item's source and first 60 characters.

## Transport model (client side)

The device never opens an inbound port; every connection it makes is
outbound.

- **HTTPS is the source of truth.** Pending commands are pulled with
  `POST rc/commands/claim` (`device_id`, `last_sequence`); results and status
  changes are pushed with `POST rc/events`; liveness is reported with
  `POST rc/heartbeat`.
- **WebSocket is notify-only.** `WakeupListener` connects with
  `python-socketio` (an optional dependency — its absence just means the
  client relies on the HTTPS reconcile loop only) and listens for a single
  event, `rc:command:available`. Receiving it only triggers an HTTPS reconcile
  early; losing the WebSocket connection never loses a command, because the
  next scheduled HTTPS reconcile still picks it up.
- **Cadence.** The coordinator's network loop reconciles and sends a heartbeat
  together roughly every `HEARTBEAT_INTERVAL_SEC` = 15 seconds
  (`core/remote_control/config.py`). A publication is considered offline after
  `OFFLINE_AFTER_SEC` = 45 seconds without a heartbeat (three missed cycles).
- **Base URL.** `RC_CONTROL_PLANE_URL` is read once, must be an absolute
  `https://` URL, and defaults to an empty string. An empty value means the
  mode is off end to end — no request is ever attempted.
- **Errors are normalized.** `ControlPlaneClient` never surfaces a response
  body; a `401`/`403` becomes `rc_unauthorized`, any other `>=400` or network
  failure becomes `rc_unavailable`, and a non-JSON body becomes
  `rc_invalid_response`. A failed claim or heartbeat just logs a warning and
  is retried on the next cycle — it never crashes the local session.

## Privacy: default deny

`core/project_config.py` is the single decision point. Every gate below
returns `False` unless the project's `.hereassistant/project.yml` opts in
explicitly (see [privacy.md](privacy.md) for the full policy format). There is
no global switch — privacy is only relaxed per project.

| Gate | Function | What it needs | What it unlocks |
|---|---|---|---|
| Presence | `can_publish_rc_presence` | `remote_control.enabled: true`, **and** either `mode: crm` + `sync.enabled: true`, **or** `mode: private` + `remote_control.allow_presence_in_private: true` | `/rc` succeeds at all; an opaque publication id, device name/kind, state, expiry and the capability flags below become visible outside the device. `mode: local` can never publish presence. |
| Remote prompts | `can_receive_remote_prompts` | active CRM channel (`mode: crm` + `sync.enabled: true` + `remote_control.enabled: true`) **and** `sync.send_prompts: true` | A command from the control plane may run as a prompt on this device (this is a remote code execution channel — see Limitations). |
| Message streaming | `can_stream_rc_messages` | active CRM channel **and** `sync.send_messages: true` | Assistant reply text (truncated to 800 chars, home directory scrubbed) and tool-call events may be emitted. |
| Diff streaming | `can_stream_rc_diffs` | active CRM channel **and** `sync.send_diffs: true` | Edit summaries (files changed / insertions / deletions and up to 50 project-relative paths) may be emitted. Diff *content* is never part of this payload. |
| Commit metadata | `can_stream_rc_commits` | active CRM channel **and** `sync.send_commits: true` | Commit SHA and message may be attached to a `rc.command_status` event. |
| Remote Git actions | `can_execute_rc_git` | active CRM channel (`mode: crm` + `sync.enabled: true` + `remote_control.enabled: true`) | `git_preflight` / `git_commit` / `git_push` may execute on this device (see below). Streaming that metadata out is still gated separately by `can_stream_rc_commits`. |

"Active CRM channel" (`_rc_crm_active`) always means `mode: crm` **and**
`sync.enabled: true` **and** `remote_control.enabled: true` together — a
`private` or `local` project can never reach any gate beyond presence.

### What a private project is allowed to leak

A `private` project with `remote_control.enabled: true` and
`remote_control.allow_presence_in_private: true` publishes presence — and
nothing else. `publications.presence_payload` for that case contains only:
opaque publication id, `privacyMode: "private"`, device id/name/kind, state,
generation, expiry, and a capability map that is entirely `false`. It never
includes the working directory, project name, repository, or provider session
id. Even the one event type a private project can emit,
`events.emit_command_status`, carries only a command id and a state
(`queued`/`running`/`succeeded`/`failed`/...) — never text, paths, or commit
metadata, since those require the separate `can_stream_rc_commits` gate that a
private project never satisfies.

Every outbound payload additionally passes through fixed scrubbing
(`core/remote_control/events.py`): the home directory is replaced with `~`,
text fields are truncated (800 characters for messages, 200 for short fields)
with a `…[truncated]` marker, tool names are reduced to safe characters, and
any path outside the project root (including anything under the home
directory) is dropped rather than sent.

## Configuration

| Setting | Where | Default | Effect |
|---|---|---|---|
| `RC_CONTROL_PLANE_URL` | environment | empty | Base URL the device's `ControlPlaneClient`/`WakeupListener` would use. Empty = mode off. Must be an absolute `https://` URL to be considered configured. |
| `RC_PROXY_CRM_BASE_URL` | environment (WebApp API process) | unset | Base URL the WebApp's server-side browser proxy calls. Unset/non-https = the `/api/rc/*` routes return `rc_not_configured` (503) without making any outbound request. |
| `RC_PROXY_CRM_TOKEN` | environment (WebApp API process) | unset | Server-held bearer token for the proxy above. Never sent to the browser. |
| `remote_control.enabled` | `.hereassistant/project.yml` | `false` | Master per-project switch; every gate above starts by checking it. |
| `remote_control.allow_presence_in_private` | `.hereassistant/project.yml` | `false` | Only meaningful for `mode: private`; lets that project publish presence-only, capability-free status. |
| `remote_control.ttl_minutes` | `.hereassistant/project.yml` | `120` | Bounded to 5–480 minutes; used to compute the publication's `expiresAt`. |

None of the three environment variables have a default domain baked into the
code or shipped in `.env.example` — every deployment supplies its own.

### Device credential

`core/remote_control/credential_store.py` defines where a device's control-plane
credential would live once something provisions one:

- macOS: `security`-backed Keychain entry (service `HereAssistant Remote
  Control`, account `device-credential`).
- Everywhere else: a JSON file at
  `<runtime>/state/remote_control/device_credential.json`, written with mode
  `0600` (the first write logs a one-time warning that this is less secure
  than an OS secret store).
- The raw credential is never written to SQLite, `.env`, or
  `project.yml` — only its presence/absence would ever be logged.
- To revoke a device, delete its credential from whichever store holds it
  (`security delete-generic-password` on macOS, or remove the JSON file) and
  revoke it on the control-plane side so a stale token cannot be reused.

As noted above, nothing in this repository currently calls `default_store()`
outside its own tests, so there is no `/rc` subcommand yet that creates,
displays, or rotates a device credential.

## Git actions

`core/remote_control/git_actions.py` executes a small set of **typed
intents**, never a shell command. Every action:

1. Is rejected outright unless `can_execute_rc_git` is `true` for the
   project's policy.
2. Resolves the target directory only through
   `project_config.project_root_for` — a trusted root explicitly configured in
   `project.yml`, never a guessed or arbitrary path.
3. Runs Git through the existing `core.git_projects.run_git`, which routes
   credentials through a credential-helper proxy and vault broker; the raw
   Git credential never appears in Python code, results, logs, or the command
   payload, and all Git output is sanitized before being reused.

| Action | What it does | Notes |
|---|---|---|
| `git_preflight` | Read-only: resolves and validates the remote URL and grant, reads the current branch, `git status --porcelain`, ahead/behind vs. upstream, and does a read-only `ls-remote` to confirm the broker/remote are reachable. | Never modifies the working tree. |
| `git_commit` | Stages **only the explicitly listed paths** (`git add -- <paths...>`) and commits with the given message. | `git add .`, `-A`, `-u`, glob characters, absolute paths, and `..` are all rejected before any Git command runs — there is no way to request a mass add. |
| `git_push` | Validates the remote name, confirms a write grant for that specific remote URL, runs `git push --dry-run` first, and only then runs the real push exactly once. | The dry run catches non-fast-forward and similar failures before anything is actually pushed. |

### Error codes

`RcGitErrorCode` is a fixed, non-extensible set:

- `AUTH_REQUIRED` — the repository grant needs (re-)authorization.
- `REMOTE_DENIED` — the remote/repository is not one this user is allowed to
  write to.
- `PREFLIGHT_FAILED` — the action itself is invalid (untrusted root, disallowed
  paths, empty commit message, generic Git failure before/without a push
  attempt) or the privacy gate denied it.
- `UNKNOWN_RECONCILE_REQUIRED` — returned **only** by `git_push`, and only when
  the real push call fails with a network/transport error *after* the
  dry-run already succeeded. At that point the push may or may not have
  reached the remote, so `git_push` does not retry automatically; it instead
  does a read-only `ls-remote HEAD` and returns that ref alongside the error so
  the caller can reconcile state manually before trying again.

## Limitations and safety boundaries

- **This is a remote code execution channel by design.** Any prompt accepted
  through `can_receive_remote_prompts` runs with the same permissions as a
  prompt typed locally into the terminal.
- `CLAUDE_PERMISSION_MODE=bypassPermissions` is forbidden project-wide (see
  the repository's `CLAUDE.md`) specifically because it would turn
  prompt-injection into unattended shell/file access; `/rc` does not change or
  bypass that policy — a remote prompt is subject to the exact same
  permission mode as a local one.
- **There is no arbitrary shell channel.** `rc_command_receipts.claim` accepts
  only a fixed set of command types — `prompt`, `stop`, `approval_decision`,
  `git_preflight`, `git_commit`, `git_push` — and rejects (`unknown_command_type`)
  anything else before it is ever executed. The browser-facing proxy narrows
  that further: it only lets a browser session create `prompt`, `stop`,
  `git_commit`, or `git_push` (`approval_decision` and `git_preflight` are
  runner-internal signals a browser never originates).
- **Duplicate delivery cannot cause a second execution.** A command receipt is
  written *before* execution starts, keyed by command id. Redelivering the
  same command id with the same payload hash is a no-op; redelivering it with
  a *different* payload hash is rejected outright (`payload_hash_mismatch`) —
  fail closed rather than silently executing a modified command under an
  old id.
