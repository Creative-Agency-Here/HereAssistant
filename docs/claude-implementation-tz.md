# ТЗ для Claude Code: HereAssistant production MVP

## 1. Цель

Сделать текущий HereAssistant production-ready MVP под чистую Ubuntu без Docker: PM2, nginx, Python venv, Nuxt production/static build, Telegram bot, Web API, Web frontend, Claude/Codex/Gemini providers, open-source hardening и privacy-first project modes. CRM должна быть только optional opt-in модулем для явно разрешённых проектов.

## 2. Что уже есть в проекте

- `bot.py`: точка входа Telegram bot, aiogram long polling, PM2-friendly restart через exit code `42`.
- `handlers/`: Telegram commands и обработка сообщений/файлов.
- `providers/`: CLI wrappers для Claude Code, Codex CLI, Gemini CLI.
- `core/config.py`: `.env` parser, runtime paths, tokens, timeouts, WebApp settings.
- `core/db.py`: SQLite schema для `users`, `accounts`, `conversations`, `messages`, `events`, `file_changes`.
- `core/events.py`: запись/чтение structured events.
- `core/changes.py`: запись full diffs в SQLite и `.runtime/logs/changes/*.md`.
- `webapp/api/server.py`: aiohttp API на `127.0.0.1:8200`.
- `webapp/api/routes/*`: `/api/health`, `/api/now`, `/api/history`, `/api/history/{conv_id}`, `/api/changes`, `/ws`.
- `webapp/front`: Nuxt 3 SPA/SSG frontend, Tailwind, markdown-it.
- `ecosystem.config.js`: PM2 config для bot/API/front, сейчас Windows-centric.
- `.env.example`, `.gitignore`, `README.md`, `ARCHITECTURE.md`, `TZ.md`, `docs/*`.

CRM-интеграция в коде не найдена. `SERVICE_API_TOKEN`, `/api/v1/tasks*`, `/api/status`, `/health` alias не найдены.

## 3. Что нельзя делать

- Не подключать OpenClaw.
- Не использовать Docker как основной путь.
- Не делать полный rewrite.
- Не делать PostgreSQL миграцию в этой итерации; текущая архитектура завязана на SQLite.
- Не включать CRM sync по умолчанию.
- Не сохранять приватные данные проектов по умолчанию.
- Не отправлять prompts/code/diffs/logs/commits/history в CRM без explicit opt-in.
- Не коммитить секреты, runtime-файлы, БД, auth files.
- Не ломать текущие Telegram commands и provider flows.
- Не убирать Claude/Codex/Gemini CLI-подход в пользу API gateway.

## 4. Задачи на итерацию MVP

### A. Ubuntu PM2 production config

Обновить `ecosystem.config.js`, чтобы он работал на Ubuntu:

- Python interpreter: `process.env.HEREASSISTANT_PYTHON || path.join(__dirname, '.venv', 'bin', 'python') || 'python3'`.
- Bot: `bot.py`, cwd root, `.runtime/logs/pm2-bot-*.log`.
- API: `webapp/api/server.py`, host `127.0.0.1`, port `8200`.
- Front production: не использовать `node.exe` и не запускать Nuxt dev как production.
- Предпочтительный путь: frontend отдаёт nginx из `webapp/front/.output/public`, PM2 front process не нужен в production.
- Если нужен PM2 preview, сделать отдельный app/env с `npm run preview -- --host 127.0.0.1 --port 3000`, но nginx static должен быть основным documented path.

### B. Linux scripts

Создать Linux-friendly bootstrap scripts без Docker:

- `scripts/bootstrap_ubuntu.sh`: install/check Python venv deps, npm deps, frontend generate/build, create `.runtime`, print next steps.
- `scripts/check_runtime.sh`: проверить CLI binaries `claude`, `codex`, `gemini`, `.env`, DB init, frontend build artifacts.
- Не hardcode private servers/domains.

### C. Healthcheck API

В `webapp/api/server.py`:

- Оставить `GET /api/health`.
- Добавить `GET /health` alias без auth.
- Добавить `GET /api/status` под обычной WebApp auth: version, db reachable, runtime dirs exist, counts summary без private contents.

### D. requirements/package fixes

- В `requirements.txt` явно добавить `aiohttp`, потому что `webapp/api/server.py` импортирует его напрямую.
- Рассмотреть pin/range versions для `aiogram`, `aiohttp`, `faster-whisper`, `Pillow`.
- Не добавлять лишние backend frameworks.
- `webapp/front/package.json`: убедиться, что `generate` и `build` documented; production docs должны использовать `npm ci` и `npm run generate` или `npm run build` согласно Nuxt static strategy.

### E. .env.example

Расширить `.env.example`:

- `TELEGRAM_BOT_TOKEN`.
- `ADMIN_IDS` и legacy `ADMIN_TELEGRAM_ID`.
- `WEBAPP_URL`, `WEBAPP_DOMAIN`, `WEBAPP_ACCESS_KEY` с warning.
- `WEBAPP_HOST=127.0.0.1`, `WEBAPP_PORT=8200`, `WEBAPP_DEV_SKIP_AUTH=0`.
- `SERVICE_API_TOKEN=` для optional service API, пустой по умолчанию.
- Provider/runtime vars: `CLI_TIMEOUT_SEC`, `CLAUDE_PERMISSION_MODE`, debug stream vars.
- Privacy defaults: default private, storage/sync defaults off.

### F. docs/ubuntu-pm2-nginx.md

Создать Ubuntu install/runbook:

- apt packages: Python 3 venv, Node LTS/npm, nginx, pm2.
- `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt`.
- `cd webapp/front && npm ci && npm run generate`.
- `.env` setup.
- `python manage.py` or CLI login steps for providers.
- `pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api`.
- `pm2 save`, `pm2 startup`.
- nginx server blocks: static frontend + reverse proxy `/api/` and `/ws` to `127.0.0.1:8200`.
- curl checks.

### G. SECURITY.md

Создать `SECURITY.md`:

- HereAssistant is remote code execution gateway by design.
- Telegram bot admin access is critical.
- Provider CLI auth homes contain credentials.
- WebApp auth modes and `WEBAPP_ACCESS_KEY` risks.
- `WEBAPP_DEV_SKIP_AUTH=1` forbidden in production.
- Logs/history/diffs may contain secrets unless privacy config disables them.
- How to report vulnerabilities.

### H. CONTRIBUTING.md

Создать `CONTRIBUTING.md`:

- Local setup.
- No secrets/runtime files.
- Run relevant checks.
- Privacy-first requirements for new features.
- Provider changes must keep CLI isolation.

### I. LICENSE

Добавить `LICENSE`. Если владелец не выбрал лицензию, использовать placeholder/TODO нельзя для open-source release. Согласовать с owner. Если нужно продолжить без вопроса, выбрать MIT только если owner явно подтвердит. В этой итерации можно создать `LICENSE` только после выбора лицензии.

### J. .gitignore hardening

Усилить `.gitignore`:

- `.env.*` но оставить `!.env.example`.
- `*.sqlite`, `*.sqlite3`, `*.db`.
- `.venv/`, `venv/`.
- `logs/`, `downloads/` вне `.runtime`.
- provider auth filenames: `credentials.json`, `.credentials.json`, `auth.json`, `oauth_creds.json`.
- `webapp/front/node_modules/`, `.nuxt`, `.output`, `dist`.
- Keep existing ignores.

### K. privacy-first project config

Добавить проектный config reader для `.hereassistant/project.yml`.

Нужные режимы:

1. `private`
   - default mode;
   - если config-файла нет, считать private;
   - не отправлять ничего в CRM;
   - не сохранять prompt/result/diff/file content в DB, если явно не разрешено.

2. `local`
   - данные можно хранить только локально в HereAssistant;
   - нельзя отправлять в CRM;
   - CRM service API не должен видеть эти проекты.

3. `crm`
   - включается только явно;
   - нужен `crm_project_id` или `crm_task_id`;
   - `sync.enabled` должен быть true;
   - наружу можно отправлять только явно разрешённые типы данных.

Пример private:

```yaml
name: "Private Project"
mode: "private"
crm_project_id: null
sync:
  enabled: false
  send_prompts: false
  send_messages: false
  send_diffs: false
  send_commits: false
  send_deploys: false
  send_artifacts: false
storage:
  save_history: false
  save_messages: false
  save_file_changes: false
```

Пример crm:

```yaml
name: "HereCRM"
mode: "crm"
crm_project_id: "herecrm"
sync:
  enabled: true
  send_prompts: false
  send_messages: false
  send_diffs: false
  send_commits: true
  send_deploys: true
  send_artifacts: false
storage:
  save_history: true
  save_messages: true
  save_file_changes: true
```

Где встроить:

- Новый модуль `core/project_config.py`: читать `Path(cwd) / ".hereassistant" / "project.yml"`, возвращать dataclass/dict с safe defaults.
- `handlers/repo.py::get_or_create_conv`: при создании conversation можно сохранять `project_name`, но privacy config должен читаться по current `cwd`, не только по DB.
- `handlers/messages.py::_process_message`: перед `repo.save_message`, `events.log("message_in")`, `events.log("message_out")`, `changes.record_edits` получить project policy.
- `handlers/repo.py::save_message`: добавить параметр/guard или вызывать только если `can_store_history`.
- `core/changes.py::record_edits`: вызывать только если `storage.save_file_changes`.
- `webapp/api/repo.py`: не отдавать private/local в CRM service endpoints; обычный WebApp owner UI может видеть local/private только после WebApp auth.
- Новый helper: `can_store_history(policy)`, `can_store_messages(policy)`, `can_store_file_changes(policy)`, `can_sync_to_crm(policy, data_type)`.

Важно: если `.hereassistant/project.yml` отсутствует или не читается, default deny для storage/sync.

### L. optional CRM task API

Добавлять только если не ломает архитектуру и privacy gates готовы.

- Service endpoints защищены `SERVICE_API_TOKEN`.
- Service token не даёт доступ к private/local projects.
- `POST /api/v1/tasks` создаёт task только для `mode: crm` project или explicit `crm_project_id`.
- `GET /api/v1/tasks*` возвращает только CRM-visible metadata, не prompts/code/diffs/logs unless allowed.
- Если текущей task-table нет, можно добавить SQLite table `tasks` минимально. Не делать PostgreSQL.

## 5. Файлы, которые нужно изменить

- `ecosystem.config.js`
  - Что: Linux-friendly PM2, убрать `node.exe` из production path.
  - Зачем: Ubuntu PM2.
  - Риск: сломать текущий Windows launch; оставить комментарий/legacy env или отдельный mode.

- `requirements.txt`
  - Что: явно добавить `aiohttp`, уточнить версии.
  - Зачем: API dependency is direct.
  - Риск: version conflicts with aiogram; проверить install.

- `.env.example`
  - Что: добавить WebApp, service token, privacy vars, Ubuntu notes.
  - Зачем: open-source bootstrap.
  - Риск: случайно предложить insecure defaults; defaults должны быть safe.

- `.gitignore`
  - Что: усилить sensitive/runtime ignores.
  - Зачем: open-source safety.
  - Риск: не исключить `.env.example`.

- `webapp/api/server.py`
  - Что: `/health`, `/api/status`, возможно service auth middleware для `/api/v1/*`.
  - Зачем: monitoring/service API.
  - Риск: accidentally expose private data; status без content.

- `handlers/messages.py`
  - Что: privacy gates перед записью `messages`, `events.payload`, `file_changes`.
  - Зачем: default private.
  - Риск: сломать history UI; в private history должна быть пустой/metadata-only.

- `handlers/repo.py`
  - Что: safe save helpers or conditional save.
  - Зачем: centralize storage decisions.
  - Риск: callers expect saved messages for history; учесть provider resume/history behavior.

- `core/changes.py`
  - Что: не писать full diff без permission.
  - Зачем: privacy.
  - Риск: `/diff` и web edits пустые для private.

- `utils/single_instance.py`
  - Что: Linux PID check.
  - Зачем: Ubuntu compatibility.
  - Риск: stale lock behavior; test manually.

- `utils/memory_link.py`
  - Что: Linux symlink support or disable on non-Windows.
  - Зачем: no `cmd /c mklink` on Ubuntu.
  - Риск: accidental deletion of memory dirs; be conservative.

- `README.md`
  - Что: update for open-source Ubuntu MVP, keep Windows notes as legacy if needed.
  - Зачем: onboarding.
  - Риск: docs drift; link detailed docs.

## 6. Файлы, которые нужно создать

- `core/project_config.py`
  - Назначение: read `.hereassistant/project.yml`, return safe policy.
  - Содержимое: YAML parser, defaults, helpers `can_store_*`, `can_sync_to_crm`.
  - Dependency: add `PyYAML` to requirements or implement minimal parser. Prefer `PyYAML` for structured YAML.

- `docs/ubuntu-pm2-nginx.md`
  - Назначение: production install/runbook for Ubuntu without Docker.

- `docs/security.md` or root `SECURITY.md`
  - Назначение: security model and reporting.
  - Requirement: root `SECURITY.md` for GitHub.

- `CONTRIBUTING.md`
  - Назначение: contribution workflow.

- `docs/providers.md`
  - Назначение: document Claude/Codex/Gemini setup and auth homes.

- `docs/privacy.md`
  - Назначение: privacy-first modes and `.hereassistant/project.yml`.

- `scripts/bootstrap_ubuntu.sh`
  - Назначение: repeatable Ubuntu setup.

- `scripts/check_runtime.sh`
  - Назначение: sanity checks.

- Optional `webapp/api/routes/status.py`
  - Назначение: keep `/api/status` logic out of `server.py`.

- Optional `webapp/api/routes/tasks.py`
  - Назначение: service task API only after privacy gates.

- `LICENSE`
  - Назначение: open-source license, only after owner/license choice.

## 7. Privacy requirements

- Default mode is `private`.
- Missing `.hereassistant/project.yml` means `private`.
- Invalid config means `private` and log warning without leaking path contents.
- CRM is opt-in only: `mode: crm` AND `sync.enabled: true` AND `crm_project_id` or `crm_task_id`.
- `private` and `local` projects are not visible through CRM/service API.
- `SERVICE_API_TOKEN` never bypasses project privacy.
- Prompts, assistant messages, code, diffs, logs, artifacts and file contents must not be stored or sent unless allowed by project config.
- For `private`, do not write `messages.content`, prompt previews, result text, full diffs, raw stream dumps by default.
- For `local`, local storage may be allowed by config, but CRM sync is always denied.
- For `crm`, each data type requires explicit flag:
  - `send_prompts`
  - `send_messages`
  - `send_diffs`
  - `send_commits`
  - `send_deploys`
  - `send_artifacts`
- Web owner UI may show local/private data only if stored locally and authenticated by Telegram/WebApp access. CRM API must not.
- Do not log `WEBAPP_ACCESS_KEY`, `SERVICE_API_TOKEN`, Telegram token, provider auth files or OAuth tokens.

## 8. API requirements

Implement only if compatible with current aiohttp structure:

- `GET /health`
  - No auth.
  - Returns `{ok, version}`.

- `GET /api/status`
  - WebApp auth.
  - Returns health/status metadata: db reachable, runtime dirs, bot version, counts.
  - Must not return prompt/result/diff/log content.

- `POST /api/v1/tasks`
  - Service auth via `Authorization: Bearer <SERVICE_API_TOKEN>`.
  - Creates CRM task only for `mode: crm`.
  - Reject private/local with 403.

- `GET /api/v1/tasks`
  - Service auth.
  - Lists only CRM-visible tasks.

- `GET /api/v1/tasks/{id}`
  - Service auth.
  - Returns metadata and only allowed fields.

- `PATCH /api/v1/tasks/{id}`
  - Service auth.
  - Update status/metadata only.

All `/api/v1/*` service endpoints are protected by `SERVICE_API_TOKEN`. If token is empty, endpoints must return 503 or be disabled, not open.

## 9. Acceptance criteria

- Fresh Ubuntu bootstrap works without Docker.
- `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` succeeds.
- `python -m compileall bot.py core handlers providers utils webapp/api` succeeds.
- `cd webapp/front && npm ci && npm run generate` succeeds.
- `pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api` brings processes online.
- `curl http://127.0.0.1:8200/health` returns ok.
- nginx serves frontend static and proxies `/api/` and `/ws`.
- Telegram bot starts and existing commands still work.
- Claude/Codex/Gemini account isolation remains via `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HOME/USERPROFILE`.
- Private project without config does not sync to CRM and does not store prompt/result/diff content by default.
- CRM service token cannot access private/local projects.
- `.env`, `.runtime`, DB, session/auth files, node_modules, build artifacts do not appear in `git status`.
- README, SECURITY, CONTRIBUTING and Ubuntu docs are updated.

## 10. Команды проверки

```bash
# Python syntax
. .venv/bin/activate
python -m compileall bot.py core handlers providers utils webapp/api

# API smoke
WEBAPP_DEV_SKIP_AUTH=1 WEBAPP_HOST=127.0.0.1 WEBAPP_PORT=8200 python webapp/api/server.py
curl -s http://127.0.0.1:8200/health
curl -s http://127.0.0.1:8200/api/health
curl -s http://127.0.0.1:8200/api/status

# Frontend
cd webapp/front
npm ci
npm run generate

# PM2
pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api
pm2 status
pm2 logs here-assistant-api --lines 50

# Service auth negative/positive
curl -i http://127.0.0.1:8200/api/v1/tasks
curl -i -H "Authorization: Bearer $SERVICE_API_TOKEN" http://127.0.0.1:8200/api/v1/tasks

# Git safety
git status --short
git ls-files | grep -E '(^\.env$|bridge\.sqlite3|\.runtime|cli_homes|node_modules|\.venv|\.output|\.nuxt|credentials\.json|auth\.json|oauth_creds\.json)' && exit 1 || true

# Secret scan quick check
rg -n "(TELEGRAM_BOT_TOKEN=.+|SERVICE_API_TOKEN=.+|WEBAPP_ACCESS_KEY=.+|hvs\\.|[0-9]+:[A-Za-z0-9_-]{30,})" .
```

## 11. Next steps, не в этой итерации

- PostgreSQL.
- Redis/queue.
- Полноценная multi-agent orchestration.
- GitHub Actions deploy.
- Full CRM sync.
- Provider marketplace.
- RBAC.
- Billing/public multi-tenant SaaS.
- Docker/Kubernetes production path.
