# Per-user Git authentication and mobile WebApp

Status: design approved for implementation; production credentials are unchanged.

Russian version: [git-auth-and-mobile-webapp.ru.md](git-auth-and-mobile-webapp.ru.md)

## Goal

Each Telegram user connects their own GitHub or Gitea identity, explicitly grants
repositories, and uses a dedicated Git broker. HereAssistant must never request a
Git password in chat or store a raw Git credential in SQLite, a project checkout,
or the coding agent's home directory.

```text
Telegram identity → HereAssistant user_id → Git connection metadata
                                             ↓
selected repositories → per-user Git broker UID → credential vault
                                             ↓
                                  authorized project roots
```

## Required security boundary

A coding agent can execute shell commands. A PAT or OAuth token placed in its
`HOME`, `.git-credentials`, environment, or SQLite can therefore be exposed by a
prompt injection or an accidental command. Production must use separate Unix
identities for code execution and authenticated Git operations:

```text
ha-alice       — Claude/Codex/Gemini, no Git credentials
ha-alice-git   — validated clone/fetch/pull/push only
```

The Git broker validates Telegram user ID, host, remote URL, project root, and the
exact operation. It never returns the token to the caller. The two UIDs share only
a per-user project group with setgid/umask; their home directories remain private.

## User experience

### WebApp as the primary flow

`Settings → Git accounts` shows provider/host, connected login, health, scopes,
granted repository count, last use, and connect/reconnect/revoke actions.

1. The user selects a configured Git host.
2. The API creates a single-use authorization session bound to Telegram `user_id`,
   a random `state`, PKCE verifier, and a maximum ten-minute lifetime.
3. Telegram opens the provider's official consent page.
4. The HTTPS callback verifies state, owner, and PKCE before exchanging the code.
5. The raw token is immediately transferred to the Git broker vault over a local
   protected channel. SQLite stores metadata and an opaque `vault_ref` only.
6. The user selects repositories and clones one as a private owned project.

All ownership checks use the user injected by Telegram Mini App middleware, never
a `user_id` accepted from the request body.

### Telegram and CLI

`/git` only reports status and opens the WebApp. It can notify about successful
connections, revocation, expiration, and push results, but never accepts a PAT,
password, or private key in a message.

`manage.py git connect` is the headless/admin fallback. GitHub may use Device Flow;
Gitea uses Authorization Code with PKCE. A manual PAT is a last resort read from a
masked TTY, never an argument, shell-history entry, or log field. CLI and WebApp
must call the same service and vault interfaces.

## Provider strategy

Gitea uses OAuth2 Authorization Code + PKCE with an operator allowlisted host.
For Gitea v1.23+, request only granular `read:user` and `write:repository` scopes;
unknown scopes are rejected because some versions can fall back to full access.

For GitHub, a GitHub App is preferred because installations select repositories
and issue short-lived, permission-scoped installation tokens. Self-hosted fallback
options are a one-time fine-grained PAT submitted over HTTPS or OAuth Device Flow
for headless CLI with an explicit warning that OAuth `repo` scope is broader.

Credentials embedded in remote URLs remain forbidden.

Primary references:

- [GitHub Apps vs OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)
- [GitHub OAuth and Device Flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
- [Gitea OAuth2 Provider](https://docs.gitea.com/development/oauth2-provider)
- [Git credential helpers](https://git-scm.com/docs/gitcredentials)

## Metadata schema

No table contains a token:

```text
git_connections
- id, user_id, provider, host
- external_user_id, external_login, avatar_url
- vault_ref, scopes_json, status, expires_at
- created_at, updated_at, last_used_at

git_repository_grants
- id, connection_id, external_repository_id
- owner_name, repository_name, clone_url, default_branch
- permission, enabled, created_at, updated_at

git_auth_sessions
- id, user_id, provider, host
- state_hash, ephemeral PKCE reference, status, expires_at, created_at
```

Authorization sessions are deleted after callback or expiry. Audit events contain
only the action, connection/project/remote identifiers, and result—never tokens,
authorization codes, or credential-bearing URLs.

## API outline

```text
GET    /api/git/connections
POST   /api/git/connections/start
GET    /api/git/oauth/callback/{provider}
POST   /api/git/connections/{id}/refresh
DELETE /api/git/connections/{id}

GET    /api/git/connections/{id}/repositories
POST   /api/git/repositories/{id}/grant
DELETE /api/git/repositories/{id}/grant
POST   /api/git/repositories/{id}/clone

GET    /api/projects
GET    /api/projects/{id}/git
POST   /api/projects/{id}/git/pull
POST   /api/projects/{id}/git/push/prepare
POST   /api/projects/{id}/git/push/confirm
```

Push confirmation is bound to user, project, commit SHA, and remote set. The broker
dry-runs every target before starting the first real push. This cannot make two
independent servers atomic, but it avoids the common partial-push authentication
failure.

## Claude-inspired mobile workspace

The supplied Claude mobile screenshots provide interaction patterns, not branding:

- a project-aware session list with live/running state and `New task` action;
- a compact sticky session/project header;
- collapsed `Read`, `Edit`, `Bash`, and `Workflow` tool rows;
- bottom sheets for command output, diffs, and background-task details;
- green/red `+N/-N` file activity;
- running/finished task groups with phases, agents, tokens, and elapsed time;
- a safe-area-aware sticky composer and large mobile touch targets.

The proposed navigation is `Sessions`, `Projects`, `Activity`, `Savings`, and
`Settings`. Existing history/edits/stats URLs remain redirects or nested views.
The UI must not display a composer, stop button, workflow, or live tool detail until
the backend can actually perform that action safely.

## Implementation phases

### P0 — current push safety

- [x] Dry-run every configured remote before the first real push.
- [x] Test that a failed preflight prevents all real pushes.
- [x] Add stable `AUTH_REQUIRED`, `REMOTE_DENIED`, and `PREFLIGHT_FAILED` errors.
- [x] Add typed metadata DTOs and migration tests with no secret columns.

### P1 — dedicated Git broker and vault

- [x] Add a separate per-user Git UID/config mapping.
- [x] Move authenticated Git subprocesses out of the code runner.
- [x] Implement a portable Git credential-helper proxy interface.
- [x] Implement the root/systemd-backed vault socket service with `SO_PEERCRED` and
  `LoadCredentialEncrypted`.
- [x] Implement owner-bound, stdin-only atomic encrypted-bundle rotation and
  controlled reload without plaintext files.
- [ ] Connect OAuth callbacks to safe encrypted-bundle rotation and service reload.
- [ ] Prove tokens cannot leak through argv, env, stdout, logs, process listing,
  RTK history, remote URLs, or the coding agent's filesystem.
- [ ] Add canary and negative cross-user tests.

### P2 — Gitea OAuth and WebApp settings

- [ ] Add owner-filtered schema, repository/service layer, and API.
- [ ] Implement exact-host Authorization Code + PKCE.
- [ ] Add Git account cards and repository picker.
- [ ] Add `/git` as the safe WebApp entry point.
- [ ] Route clone/pull/push through repository grants and the broker only.

### P3 — GitHub

- [ ] Implement GitHub App installation as the preferred flow.
- [ ] Add one-time fine-grained PAT fallback for self-hosted instances.
- [ ] Add headless Device Flow with scope warning.
- [ ] Cover organization approval, refresh/expiry, and revoke.

### P4 — mobile workspace

- [ ] Refactor navigation without changing existing API semantics.
- [ ] Add real session/project cards and status indicators.
- [ ] Add bottom sheets for existing file changes and tool summaries.
- [ ] Add workflow UI only after typed backend events exist.
- [ ] Probe iOS/Android safe areas, keyboard, WebView, and deep links.

## Acceptance criteria

- Connections, grants, repositories, and projects are owner-isolated.
- A token never reaches Telegram, SQLite, remote URL, project tree, agent home,
  application logs, command arguments, or RTK history.
- The code runner cannot retrieve a raw credential from the Git broker.
- Clone/pull/push require both a repository grant and an authorized project root.
- Revocation blocks subsequent Git operations immediately.
- Users can independently work with repositories that have identical names.
- WebApp actions reflect real backend capabilities on Telegram, mobile browser,
  and desktop browser.
