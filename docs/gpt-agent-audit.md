# HereAssistant audit

## Summary

HereAssistant сейчас является рабочим Telegram-to-CLI gateway: `bot.py` поднимает aiogram long polling, маршруты лежат в `handlers/`, провайдеры CLI в `providers/`, состояние хранится в SQLite `bridge.sqlite3`, web API на aiohttp читает ту же БД, фронт находится в `webapp/front` как Nuxt 3 SPA/SSG.

Проект исторически ориентирован на Windows Server/PM2. Под чистую Ubuntu без Docker он близок по общей модели процессов, но не готов из коробки: есть `node.exe` в PM2 config, `start_bot.bat`, Windows-only `tasklist`, `cmd /c mklink /J`, Windows-пути в README/TZ/scripts и deploy-скрипты со старыми серверами.

CRM-интеграция в коде не найдена. Это хорошо для privacy-first MVP: CRM надо добавлять как optional opt-in модуль, а не как дефолтное поведение.

Главный security/privacy риск: по умолчанию сохраняются user prompts и assistant responses в `messages`, превью промптов в `events.payload`, diff правок в `file_changes` и `.runtime/logs/changes/*.md`. Web API отдаёт историю и diff через Telegram initData или `WEBAPP_ACCESS_KEY`. Для open-source/product режима это нужно сделать настраиваемым по проекту и запретить для private/local.

## Current architecture

### Процессы

- Telegram bot: `bot.py`, aiogram long polling, запускается напрямую `python bot.py` или через PM2 app `here-assistant-bot`.
- Web API: `webapp/api/server.py`, aiohttp, порт по умолчанию `127.0.0.1:8200`, PM2 app `here-assistant-api`.
- Frontend: `webapp/front`, Nuxt 3 SPA/SSG. В текущем PM2 config запускается dev server на порту `3000` через `node.exe node_modules/nuxt/bin/nuxt.mjs dev --port 3000`.
- CLI/account manager: `manage.py`, интерактивное меню добавления аккаунтов, npm/pip install, login CLI, старт бота.
- Вспомогательный restart flagger: `restart_bot.py`, пишет `.runtime/state/restart_request.json`.
- PM2 config: `ecosystem.config.js` поднимает bot, API и Nuxt dev server; комментарии и front script Windows-specific.

Windows-specific части найдены:

- `ecosystem.config.js`: `script: 'node.exe'`, комментарии про Windows.
- `start_bot.bat`, `webapp/front/start-dev.bat`.
- `utils/single_instance.py`: проверка PID через `tasklist`.
- `utils/memory_link.py`: объединение памяти через `cmd /c mklink /J`.
- `README.md`, `TZ.md`, `docs/miniapp-tz.md`: Windows Server пути и команды.
- `scripts/deploy.py`, `scripts/setup_assistant.py`: deploy с Windows-машины, hardcoded `185.246.220.120`, `root`, scp/ssh/tar.

### Backend

- Язык/фреймворки: Python, aiogram 3 для Telegram, aiohttp для Web API, sqlite3 стандартной библиотеки.
- Конфиг: `core/config.py` сам читает `.env`, задаёт пути `.runtime`, `workspace/default`, `bridge.sqlite3`, timeout и WebApp settings.
- БД: `core/db.py` создаёт schema и простую миграцию `conversations.project_name`.
- Логи: `core/logging_setup.py` пишет `.runtime/logs/bot.log` с `TimedRotatingFileHandler`.
- События: `core/events.py` пишет в таблицу `events`.
- File changes: `core/changes.py` пишет full unified diff в таблицу `file_changes` и `.runtime/logs/changes/YYYY-MM-DD.md`.
- Обработка сообщений: `handlers/messages.py` принимает текст и вложения, делает debounce, транскрибирует voice через `utils/whisper.py`, отменяет предыдущую активную задачу в том же chat/thread, запускает provider, редактирует Telegram live progress, сохраняет историю/события/правки.
- Timeout: `config.CLI_TIMEOUT`, default `1800` секунд, используется в `providers/base.py`, `providers/claude_code.py`, `providers/gemini.py`.
- CWD/workspace: default `core/config.py::DEFAULT_CWD`, по умолчанию `workspace/default`; команды `/cwd` и `/project` меняют `conversations.cwd`.

### Frontend

- Фреймворк: Nuxt 3.17.7, `ssr: false`, `nitro.preset: static`, Tailwind, markdown-it.
- Запуск dev: `webapp/front/package.json` script `dev`: `nuxt dev --port 3000`.
- Production build: `build`: `nuxt build`, `generate`: `nuxt generate`, статика в `.output/public` по `webapp/README.md`.
- API base: `NUXT_PUBLIC_API_BASE`, default `http://127.0.0.1:8200`.
- Авторизация frontend: `useApi.ts` шлёт `Authorization: tma <initData>` или `X-Access-Key`; `useLiveLog.ts` передаёт `?tma=` или `?key=` в WebSocket.
- Страницы: `/` current status/log, `/history`, `/history/[id]`, `/edits`. `/stats` и `/settings` в UI помечены disabled, страниц не найдено.

### Providers

#### Claude Code CLI

- Файл: `providers/claude_code.py`.
- Env: `CLAUDE_CONFIG_DIR=<account.cli_home_path>`.
- Команда: `claude --print --output-format stream-json --verbose --include-partial-messages --permission-mode <CLAUDE_PERMISSION_MODE|acceptEdits> --append-system-prompt <RU_SYSTEM_INSTRUCTION> [--model X] [--resume session]`.
- Prompt: через stdin.
- Output: построчный stream-json stdout, stderr отдельной задачей.
- Session: `system/init.session_id` или `result.session_id`, сохраняется в `conversations.provider_session_id`.
- File edits: парсятся tool_use `Edit/Write/MultiEdit/NotebookEdit`, old/new попадают в meta.
- Debug dump: `.runtime/logs/claude-stream-*.jsonl` при `CLAUDE_DEBUG_STREAM=1`.

#### Codex CLI

- Файл: `providers/codex.py`.
- Env: `CODEX_HOME=<account.cli_home_path>`.
- Команда: `codex exec [resume <sid>] --skip-git-repo-check [-c model=...] -c instructions=... <full_prompt>`.
- Prompt: аргумент командной строки, не stdin.
- Output: stdout целиком, stream не реализован.
- Session: эвристика по stdout/stderr: токен с `session`/`id`, длиной >=16 и дефисом.
- Риск: длинный prompt как argv может упереться в лимит shell/OS.

#### Gemini CLI

- Файл: `providers/gemini.py`.
- Env: `HOME=<cli_home>`, `USERPROFILE=<cli_home>`, `GEMINI_CLI_TRUST_WORKSPACE=true`.
- Команда: `gemini --skip-trust --approval-mode yolo -o stream-json -p "" [-m model]`.
- Prompt: stdin, включает `RU_SYSTEM_INSTRUCTION`, Claude memory snapshot и user prompt.
- Output: postрочный stream-json stdout.
- Session: native resume не используется; возвращает `None`, история идёт через `build_prompt_with_history`.
- Memory: читает `cli_homes/claude_code__*/projects/<encoded-cwd>/memory/*.md`.
- Debug dump: `.runtime/logs/gemini-stream-*.jsonl` при `GEMINI_DEBUG_STREAM=1`.

Другие providers не найдены. OpenClaw не найден.

## Entrypoints

- `bot.py`: основной Telegram bot.
- `webapp/api/server.py`: aiohttp Web API.
- `manage.py`: интерактивный локальный менеджер аккаунтов/зависимостей/старта.
- `restart_bot.py`: внешний запрос рестарта через flag-файл.
- `scripts/deploy.py`: deploy frontend на hardcoded server.
- `scripts/setup_assistant.py`: настройка nginx/SSL на hardcoded server.
- `ecosystem.config.js`: PM2 apps.
- `webapp/front/package.json`: Nuxt scripts.
- `start_bot.bat`, `webapp/front/start-dev.bat`: Windows launchers.

## API routes

Текущие routes в `webapp/api/server.py`:

- `GET /api/health`: без авторизации, `{"ok": true, "version": "0.1.0"}`.
- `GET /api/now`: активная задача + последние действия, требует auth.
- `GET /api/history`: список conversations, требует auth.
- `GET /api/history/{conv_id}`: conversation + messages, требует auth.
- `GET /api/changes`: file_changes, требует auth.
- `GET /ws`: WebSocket логов и статуса, требует auth через `?tma=` или `?key=`.

Routes `/health`, `/api/status`, `/api/v1/tasks*`, service token endpoints не найдены.

## Telegram handlers

Роутеры подключены в `handlers/__init__.py`.

Команды:

- `handlers/admin_claim.py`: `/start` claim/admin greeting.
- `handlers/accounts.py`: `/accounts`, `/account`, callbacks `acc:use:*`, `acc:cancel`.
- `handlers/models.py`: `/model`, callbacks `mdl:set:*`, `mdl:cancel`.
- `handlers/projects.py`: `/cwd`, `/where`, `/project list|new|use`.
- `handlers/system.py`: `/web`, `/help`, `/status`, `/version`, `/new`, `/reset`, `/delete`, callbacks reset/delete.
- `handlers/stats.py`: `/stats`, `/log`.
- `handlers/deploy.py`: `/deploy`.
- `handlers/diff.py`: `/diff`.
- `handlers/messages.py`: non-command messages and attachments: text, document, photo, audio, voice, video, video_note.

Admin check:

- Message handlers используют `handlers/common.py::is_admin`, сравнивает только `config.ADMIN_ID`.
- Web auth поддерживает `ADMIN_IDS`, но Telegram handlers фактически ориентируются на один `ADMIN_ID`. Это неоднозначность для future multi-admin.

## Providers

См. раздел Current architecture / Providers. Все три CLI запускаются через `asyncio.create_subprocess_exec`; Windows `.cmd`/`.bat` обёртки обрабатываются в `providers/base.py`, `providers/claude_code.py`, `providers/gemini.py`.

Опасные режимы:

- Claude default `CLAUDE_PERMISSION_MODE=acceptEdits`.
- Gemini `--approval-mode yolo`.
- Codex `--skip-git-repo-check`.

Это соответствует coding gateway, но требует явного warning в `SECURITY.md`.

## Database

SQLite file: `core/config.py::DB_PATH = BASE_DIR / "bridge.sqlite3"`.

Schema создаётся в `core/db.py::SCHEMA`:

- `users`: `telegram_id`, `username`, `role`, `created_at`.
- `accounts`: `id`, `provider`, `label`, `cli_home_path`, `default_model`, `enabled`, `notes`.
- `conversations`: `id`, `user_id`, `chat_id`, `thread_id`, `account_id`, `model`, `provider_session_id`, `cwd`, `project_name`, `created_at`, `updated_at`, unique `(chat_id, thread_id)`.
- `messages`: `id`, `conversation_id`, `role`, `content`, `provider`, `model`, `created_at`.
- `events`: `id`, `timestamp`, `event_type`, `user_id`, `chat_id`, `thread_id`, `account_label`, `provider`, `model`, `tokens_in`, `tokens_out`, `duration_ms`, `payload`.
- `file_changes`: `id`, `ts`, `thread_id`, `account`, `model`, `file`, `tool`, `added`, `removed`, `diff`.

Миграции:

- Только `MIGRATIONS = [("conversations", "project_name", ...)]`.
- Версионированные migration files не найдены.

Где пишется история:

- `handlers/repo.py::save_message`: `messages`.
- `handlers/messages.py`: `events.log("message_in")`, `events.log("message_out")`, `events.log("error")`.
- `core/changes.py::record_edits`: `file_changes` и `.runtime/logs/changes/*.md`.
- `handlers/deploy.py`: `events.log("deploy_initiated")`, `events.log("deploy_completed")`.

Где читается история:

- `handlers/repo.py::build_prompt_with_history`.
- `webapp/api/repo.py::list_conversations`, `get_conversation`, `list_file_changes`.
- `handlers/diff.py`: последний `events.payload` для `/diff`.

## Runtime files

Создаются/используются:

- `.runtime/downloads/`: Telegram attachments.
- `.runtime/logs/bot.log`: application logs.
- `.runtime/logs/bot.log.YYYY-MM-DD`: rotation.
- `.runtime/logs/changes/YYYY-MM-DD.md`: full diffs.
- `.runtime/logs/claude-stream-*.jsonl`: optional raw Claude stream.
- `.runtime/logs/gemini-stream-*.jsonl`: optional raw Gemini stream.
- `.runtime/backups/bot-*.py`: backup before `/deploy`.
- `.runtime/state/restart.json`, `restart_request.json`, `restart_count.json`, `bot.lock`, `snapshot.json`, `snapshot_full.json`.
- `.runtime/cli_homes/<provider>__<label>/`: provider auth/session/config homes.
- `workspace/default` and `workspace/<project>`: working directories.
- `memory/`: shared Claude memory target.
- `bridge.sqlite3`: persistent SQLite DB.
- `webapp/front/.nuxt`, `.output`, `node_modules`: frontend dev/build artifacts.

Нельзя коммитить: `.env`, `.env.*` with secrets, `bridge.sqlite3`, `.runtime/`, `workspace/`, `memory/`, `cli_homes`, provider auth files, logs, downloads, `.nuxt`, `.output`, `dist`, `node_modules`, `.venv`, `__pycache__`.

## Security risks

1. `messages.content` сохраняет полный prompt/result по умолчанию. Для private project это неприемлемо без opt-in.
2. `events.payload.text_preview` сохраняет первые 500 символов prompt.
3. `events.payload.edits` сохраняет old/new snippets до 2000 символов.
4. `file_changes.diff` и `.runtime/logs/changes/*.md` сохраняют full diff.
5. `providers/base.py` логирует `prompt_preview` для длинного последнего argv; для Codex prompt передаётся как argv, значит часть prompt может попасть в `bot.log`.
6. Web API отдаёт историю и diff. Auth есть, но `WEBAPP_ACCESS_KEY` передаётся в URL `?key=` для menu/web links и может попасть в browser history, reverse proxy logs, Telegram URL context.
7. `WEBAPP_DEV_SKIP_AUTH=1` полностью отключает auth; нужен explicit production guard/docs.
8. `CLAUDE_DEBUG_STREAM`/`GEMINI_DEBUG_STREAM` могут писать raw events, включая tool inputs/results, в `.runtime/logs`.
9. Provider auth homes содержат Claude/Codex/Gemini credentials/OAuth tokens.
10. `scripts/deploy.py` и `scripts/setup_assistant.py` содержат hardcoded server IP/user/path; секретов не видно, но для open-source надо вынести в docs/examples или env.
11. `utils/single_instance.py` на Linux сейчас считает PID alive при ошибке `tasklist`, что может заблокировать запуск stale lock.
12. `utils/memory_link.py` использует `os.system` с quoted paths; на Linux не работает, на Windows потенциально чувствительно к path quoting.
13. Auth в Telegram handlers проверяет только `ADMIN_ID`, а web auth ещё и `ADMIN_IDS`; модель доступа неоднозначна.
14. Service token/API for CRM не найден. Если добавить, важно не дать ему читать private/local history.

## Ubuntu readiness

Что готово:

- Python backend в целом кроссплатформенный: aiogram/aiohttp/sqlite/pathlib.
- Providers на Linux могут запускать `claude`, `codex`, `gemini` напрямую без `.cmd` workaround.
- Frontend package имеет `build/generate/preview`.
- `.gitignore` уже исключает `.env`, `.runtime/`, `bridge.sqlite3`, `workspace/`, `node_modules`, `.nuxt`, `.output`, `dist`, `memory`, `.claude`.
- `.env.example` есть.

Что не готово:

- `ecosystem.config.js` front использует `node.exe` и dev server как production.
- Нет отдельного Linux/production PM2 config для `nuxt preview` или static `.output/public`.
- `utils/single_instance.py` использует Windows `tasklist`; на Ubuntu нужен `os.kill(pid, 0)` или `/proc`.
- `utils/memory_link.py` использует Windows junction `cmd /c mklink /J`; на Ubuntu нужен symlink или отключение.
- `start_bot.bat`, `webapp/front/start-dev.bat` Windows-only.
- `README.md`, `TZ.md`, `webapp/README.md`, `docs/miniapp-tz.md` описывают Windows.
- `scripts/deploy.py`, `scripts/setup_assistant.py` hardcoded to old server flow; не являются чистым Ubuntu bootstrap.
- `requirements.txt` не указывает `aiohttp`, хотя импортируется напрямую. Сейчас aiohttp приходит транзитивно через aiogram, но для production лучше указать явно.
- `requirements.txt` не фиксирует версии и не содержит `python-dotenv` не нужен, потому что parser самописный.
- Health route есть только `/api/health`, не `/health`.
- Nginx config для Ubuntu не найден.
- PM2 startup/runbook для Ubuntu не найден.

Минимально изменить:

- `ecosystem.config.js`: сделать Linux-friendly `python3`, API, bot, frontend production static/preview или убрать front dev из production.
- `utils/single_instance.py`: добавить Linux PID check.
- `utils/memory_link.py`: Linux symlink branch или feature flag disable.
- `requirements.txt`: явно добавить `aiohttp`.
- Docs: `docs/ubuntu-pm2-nginx.md`, README quickstart for Ubuntu.
- Add `/health` alias and `/api/status` if нужно для nginx/PM2 monitoring.

## Open-source readiness

Найдено:

- `README.md`: есть, но Windows-centric.
- `.env.example`: есть.
- `.gitignore`: есть.
- `docs/`: есть.
- `webapp/README.md`: есть.

Не найдено:

- `LICENSE`.
- `SECURITY.md`.
- `CONTRIBUTING.md`.
- `docs/install`.
- `docs/architecture` как отдельный open-source doc; есть `ARCHITECTURE.md` в корне.
- `docs/security`.
- `docs/providers`.
- `docs/ubuntu-pm2-nginx`.

Проверка secret/runtime files:

- `git ls-files` не показывает `.env`, `bridge.sqlite3`, `.runtime`, node_modules, provider auth files.
- `find` на глубине 4 нашёл только `.env.example` из sensitive patterns.
- Явных токенов/ключей в tracked files не найдено, но deploy scripts содержат IP/user/root/path и email.

Риски open-source:

- `scripts/deploy.py` и `scripts/setup_assistant.py` привязаны к конкретному серверу/IP/domain/email.
- Документация содержит старые инфраструктурные имена/серверы (`DE-1`, `185.246.220.120`, `assistant.hereagency.ru`).
- `.env.example` не содержит `WEBAPP_ACCESS_KEY`, `WEBAPP_URL`, `SERVICE_API_TOKEN`, privacy config variables.
- `.gitignore` не исключает `*.sqlite`, `*.db`, `.env.*`, `.venv`, `downloads/`, `logs/` outside `.runtime`, provider credential filenames explicitly.

## Recommended MVP scope

1. Ubuntu PM2 production readiness без Docker: venv, npm build/generate, PM2 bot/API, nginx static + reverse proxy.
2. Linux compatibility fixes: PID lock, memory links.
3. Security docs and hardening: `SECURITY.md`, safer `.env.example`, `.gitignore`.
4. Privacy-first project config `.hereassistant/project.yml`.
5. Storage guards: default private не пишет prompt/result/diff/file content в DB, если не разрешено.
6. CRM opt-in gates: CRM endpoints/tasks видят только `mode: crm` + `sync.enabled: true` + allowed data types.
7. Minimal service API behind `SERVICE_API_TOKEN` only after privacy gates.

## Questions for owner

1. Какой домен планируется для open-source HereAssistant production: оставить `assistant.hereagency.ru` только как example или убрать полностью из дефолтов?
2. Нужен ли `WEBAPP_ACCESS_KEY` в production, или доступ только через Telegram Mini App initData?
3. В режиме `private` нужно вообще не писать `messages`, или можно хранить только metadata без content?
4. Для `local` можно ли хранить full diffs локально, или только summary без содержимого?
5. CRM task API должен создавать Telegram conversation/task внутри текущей модели SQLite или отдельную таблицу `tasks`?
6. Multi-admin (`ADMIN_IDS`) нужен в Telegram handlers в MVP или оставить single-admin?
7. Общая Claude memory через symlink на Ubuntu нужна в MVP или можно временно отключить?
