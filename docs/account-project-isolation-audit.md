# Account and project isolation audit

Status: deployed to production in Hardening 0.4.0 on 2026-07-12.

Application-level user, account, project, conversation, memory and WebApp
isolation is active. OS-level process isolation remains pending because all
provider processes currently run under Unix user `here`.

## Scope and findings

This iteration closes application-level cross-user paths without PostgreSQL, Docker, new filesystem roots, or OS-level runners.

| Boundary | Previous behaviour | New behaviour |
|---|---|---|
| Provider selection | Own, then `owner_user_id IS NULL`, then any enabled account | Own, then `shared=1`, otherwise `ACCOUNT_NOT_AVAILABLE` |
| Unassigned account | `owner_user_id=NULL` implicitly meant shared | `NULL` means unassigned and inaccessible |
| Account picker | Listed and accepted every enabled account | Queries and callback IDs are scoped to the requesting user |
| Telegram `cwd` | Any existing absolute directory | Existing path inside the selected authorized project root |
| Projects | Directories without an access model | `projects` plus explicit `project_members` |
| Conversation | `UNIQUE(chat_id, thread_id)` | `UNIQUE(user_id, chat_id, thread_id)` |
| Debounce/tasks | Keyed by chat and thread | Keyed by user, chat, and thread |
| WebApp history | Returned every conversation | Scoped to the authenticated user |
| Gemini memory | Globbed every `claude_code__*` home | Same owner's Claude home and an authorized project only |

`/diff` and the WebApp active-task lookup now include `user_id` as well.

## Changed code

- `core/db.py`: explicit sharing, project tables, and conversation migration.
- `core/projects.py`: registration, membership lookup, and `resolve_authorized_project_path()`.
- `handlers/repo.py`: fail-closed account selection and user-scoped conversations.
- `handlers/accounts.py`, `handlers/models.py`, `handlers/messages.py`: scoped account access.
- `handlers/projects.py`: registered-project selection and authorized `cwd`.
- `handlers/message_state.py`: per-user debounce and active-task identity.
- `providers/base.py`, `providers/__init__.py`, `providers/gemini.py`: caller identity and owner-scoped memory.
- `manage.py`, `manage_accounts.py`, `manage_actions.py`, `manage_header.py`: owner/shared and trusted-project administration.
- `webapp/api/repo.py`, `webapp/api/routes/history.py`: user-scoped WebApp history.

## SQLite migration

`db.init()` performs the following transactionally:

1. Adds `accounts.shared INTEGER NOT NULL DEFAULT 0`.
2. Creates `projects` and `project_members`.
3. Adds `conversations.project_id`.
4. Rebuilds `conversations` with `UNIQUE(user_id, chat_id, thread_id)`, preserving primary keys and existing message links.

No legacy account becomes shared and no arbitrary legacy `cwd` becomes trusted automatically. Direct child directories of `workspace/<user_id>/` may be safely registered as personal projects. A legacy conversation pointing elsewhere is reset to the user's `default` project and its provider session is cleared.

Before a future production restart, use `python manage.py`:

1. Open **Settings → Account access**.
2. Assign every legacy account to the correct Telegram user, or deliberately mark it shared.
3. Use **Settings → Register project** for trusted repositories outside the personal workspace.
4. Verify that every user has an accessible account and project.

Until step 2 is completed, the legacy production account is intentionally fail-closed.

## Path authorization

`resolve_authorized_project_path(user_id, project_id, requested_path)` loads an owned project or a shared project with explicit membership, resolves root and request with `Path.resolve(strict=True)`, and accepts only the root or a descendant. Missing paths, `..`, escaping symlinks, foreign projects, and shared projects without membership are rejected.

The terminal `chat.py` keeps unrestricted `/cwd` because it is a trusted local operator tool, not a remote Telegram boundary. It must not be exposed as a multi-user network service.

Telegram repository operations use the same boundary: clone URLs must use HTTPS or `git@host` and match `GIT_ALLOWED_HOSTS`; embedded credentials are rejected. Worktrees live below the requesting user's hidden `.worktrees` directory and are registered as separate private project roots. Push is available only after a user-bound confirmation callback.

## Compatibility and remaining limits

- A legacy unassigned account stops working until ownership or explicit sharing is configured.
- Existing arbitrary `/cwd` values are not trusted after migration.
- All CLI processes still run as Unix user `here`. Application checks do not protect against a compromised provider process.
- `chmod 700` cannot separate processes running with the same Unix UID.
- Before private client repositories are added, execution should move to restricted per-user runners such as `ha-user-a` and `ha-user-b`.
- WebSocket task status and `file_changes` are filtered by authenticated Telegram user. Raw global `bot.log` is available only to the primary administrator; other approved users receive an empty log feed.

## Verification and RTK prerequisites

Tests cover account fallback, forged access, unassigned accounts, conversation identity, explicit project membership, traversal, symlink escape, per-user buffering, migration preservation, and cross-profile memory rejection.

The release criterion is:

```bash
scripts/quality_gate.sh
```

RTK must remain absent until every production account has an owner or deliberate shared flag, trusted projects are registered, the migration is backed up and rehearsed, and a dedicated Claude canary profile exists.
