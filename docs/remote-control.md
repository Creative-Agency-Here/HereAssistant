# Remote control (`/rc`)

Russian version: [remote-control.ru.md](remote-control.ru.md)

`/rc` lets you publish the live terminal-chat session you are currently running
(`chat.py`) so it can be observed and, if the project's privacy policy allows it,
driven from a browser instead of typing into the terminal. It is off by default
for every project and stays off unless you opt in explicitly.

## Current implementation status

Read this before relying on `/rc`:

- **Works locally with no network:** `/rc`, `/rc status`, `/rc off`, `/rc stop`,
  the privacy gates and the single queue that arbitrates local and remote input.
  With no control plane configured, publishing writes a row into the local
  `rc_publications` table and makes no network request at all.
- **Verified end to end (2026-07-30):** the full loop ran against a real control
  plane — the device exchanged its credential for a token, published a session, the
  Telegram bot saw that publication as live, queued a `prompt` command and the device
  received it. Before that the channel was dead: runner routes sat behind the user
  guard, so the device token never reached them.
- **Wired to the network:** when both the control-plane base URL and a device
  credential are present, the terminal chat builds a client, claims commands,
  executes them (`prompt`, `stop`, `git_preflight`, `git_commit`, `git_push`,
  `approval_decision`) and drains the event queue in the background. An empty URL
  or a missing credential keeps the whole mode off.
- **Ready on the browser side:** `webapp/api/routes/remote_control.py` is the
  proxy an authenticated WebApp session uses to list publications and queue
  commands; the UI lives in `webapp/front/components/remote-control/` and
  `webapp/front/composables/useRemoteControl.ts`.
- **Not part of this repository:** the control-plane server itself. HereAssistant
  ships the device and browser sides; a server implementing the contract
  described here is deployed separately.
- **Deliberately not wired:** token-by-token response streaming (`emit_progress`)
  and the approval-request event (`emit_approval_required`). None of the
  supported providers asks for tool approval at runtime, so there is no source
  for such events on the device. That is also why `approval_decision` always
  answers with a refusal rather than an approval.

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
  the device exchanges its credential for a short-lived access token
  (`POST cli-agent/runner/exchange`), publishes the session
  (`POST cli-agent/runner/publications`) and then works against the returned
  publication id: list commands with
  `GET cli-agent/runner/publications/:id/commands`, claim each one with
  `POST .../commands/:commandId/claim`, report its outcome with
  `POST .../commands/:commandId/result`, push events in batches with
  `POST .../events`, report liveness with `POST .../heartbeat` and withdraw the
  publication with `DELETE cli-agent/runner/publications/:id`.
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
| `RC_PROXY_CRM_OWNER_USER_ID` | environment (WebApp API process) | unset | CRM id of the OWNER of the token above. The proxy compares it with the browser session's `crm_user_id`: another participant's session gets `not_owner` (403) before any outbound request, unset means `rc_not_configured` (503). Without this check any workspace member signed in via SSO could run code on the owner's machine. |
| `remote_control.enabled` | `.hereassistant/project.yml` | `false` | Master per-project switch; every gate above starts by checking it. |
| `remote_control.allow_presence_in_private` | `.hereassistant/project.yml` | `false` | Only meaningful for `mode: private`; lets that project publish presence-only, capability-free status. |
| `remote_control.ttl_minutes` | `.hereassistant/project.yml` | `120` | Bounded to 5–480 minutes; used to compute the publication's `expiresAt`. |

None of the environment variables have a default domain baked into the
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
- **The browser proxy is owner-only.** A session with `auth_source='crm'` is
  handed to any workspace member who exchanges their own `hat_` ticket, while the
  outbound request carries the shared server-side `RC_PROXY_CRM_TOKEN`, i.e. acts
  as the device owner. The proxy therefore compares the session's `crm_user_id`
  with `RC_PROXY_CRM_OWNER_USER_ID`: a mismatch is `not_owner` (403) before any
  outbound request, and an unset variable is `rc_not_configured` (503). Without
  that check an unrelated member could run code in the owner's working directory,
  and the contract has no command cancellation.
- **Duplicate delivery cannot cause a second execution.** A command receipt is
  written *before* execution starts, keyed by command id. Redelivering the
  same command id with the same payload hash is a no-op; redelivering it with
  a *different* payload hash is rejected outright (`payload_hash_mismatch`) —
  fail closed rather than silently executing a modified command under an
  old id.
