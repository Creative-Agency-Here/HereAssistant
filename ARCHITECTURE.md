# HereAssistant — что под капотом

Документ описывает не «что лежит в какой папке» (это в `README.md`), а **как всё это работает в рантайме**: жизненный цикл одного сообщения, стриминг, прерывания, дебаунс, общая память, самоперезапуск, версионирование.

---

## 1. Высокоуровневая схема

```
Telegram ──▶ aiogram (long polling)
                │
                ▼
         handlers/messages.py
         (дебаунс → flush → отмена предыдущей → запуск задачи)
                │
                ▼
         providers/<cli>.py
         (subprocess: claude / codex / gemini, --print, stream-json)
                │
                ├──▶ stdout: stream JSON events  ─▶ progress callback ─▶ edit Telegram-сообщения
                ├──▶ stderr: буфер для отчёта об ошибках
                └──▶ финал → save_message + events.log
                                │
                                ▼
                       bridge.sqlite3
                       (conversations · messages · events · accounts · users)
```

Точка входа — `bot.py` (тонкая, ~170 строк). Вся бизнес-логика разнесена по `core/`, `providers/`, `handlers/`, `utils/`.

---

## 2. Жизненный цикл одного сообщения

Происходит в `handlers/messages.py`. Пошагово:

### 2.1. Приём апдейта
`handle_any` ловит `F.text | F.document | F.photo | F.audio | F.voice | F.video | F.video_note`. Команды (`/...`) сразу отдаются другим роутерам.

Проверка `is_admin` — единственный админ-id из `.env` или из claim'a.

### 2.2. Дебаунс склейки длинных сообщений
Telegram режет тексты длиннее ~4096 символов на несколько апдейтов. Чтобы не запускать CLI трижды на одно длинное сообщение, есть буфер `_pending[(chat_id, thread_id)]`:

- каждое поступившее сообщение откладывает таймер `DEBOUNCE_SEC` (по умолчанию 1.5с);
- по таймауту вызывается `_flush_pending`, который склеивает накопленные тексты и вложения в один промпт.

### 2.3. Голосовые → текст
Если среди вложений есть `.ogg`/`.oga`/`.mp3`/... — `utils/whisper.transcribe(path)` расшифровывает их и подмешивает в текст промпта. Расшифровка показывается отдельным сообщением «🎙 расшифровано: ...».

### 2.4. Прерывание предыдущей задачи
В словаре `_active_tasks[(chat_id, thread_id)] = asyncio.Task` хранится текущая задача для треда. Если приходит новое сообщение и предыдущая ещё работает:

- `INTERRUPT_ON_NEW=1` (default): `prev.cancel()` → внутри провайдера `subprocess.kill()`, в чат — «⏸ Предыдущий запрос прерван».
- `INTERRUPT_ON_NEW=0`: «⏳ Уже выполняю — поставил в очередь» (фактически новая задача всё равно создаётся, без сериализации; режим оставлен на будущее).

### 2.5. Создание задачи
Запускается `_process_message`:

1. Логируется `events.log("message_in")`.
2. Параллельно стартует **typing-heartbeat** (`bot.send_chat_action("typing")` каждые ~4с) и **heartbeat-таска**, которая каждую секунду перерисовывает прогресс-сообщение (чтобы секундомер шёл даже без новых событий от CLI).
3. Создаётся «live message» — обычное сообщение Telegram, которое мы будем редактировать. Шапка: `🤖 модель · 👤 аккаунт · 📝 заметка · ⌛ Nс · 🔧 текущий_tool`.
4. Дальше: `providers.make(account).run(prompt, cwd, session_id, model, attachments, progress=cb)`.

### 2.6. Стриминг прогресса
Провайдер (например, `ClaudeCodeProvider`) дёргает callback `progress_cb(partial_text, event_type, meta)` на каждом интересном событии stream-json. `meta` несёт `tool_call_log` (цепочка вызовов инструментов с короткими описаниями), `current_tool`, `tool_uses`, `edits` (Edit/Write — со счётчиком +/− строк).

Редактирование Telegram-сообщения дросселируется:

- минимум `PROGRESS_MIN_INTERVAL_SEC` (1.5с) между edit'ами;
- при `TelegramRetryAfter` (flood control) — пауза на присланный `retry_after`;
- если текст превысил `PROGRESS_MAX_CHARS` (3500) — флаг `overflowed`, новые edit'ы прекращаются, финал придёт отдельным сообщением через `send_long`.

### 2.7. Финал
После возврата из `prov.run()`:

1. Сохраняем ответ в `messages`.
2. Обновляем `provider_session_id` в `conversations`, если поменялся.
3. Пишем `events.log("message_out", tokens_in, tokens_out, duration_ms, edits, tool_uses)`.
4. Собираем финальный HTML: шапка + ответ (через `markdown_to_html`) + блок «📋 Шаги (N)» в `<blockquote expandable>` + подпись `модель · Nс · +X −Y строк · Z файлов · обновлено HH:MM:SS`.
5. Если влезает в 4000 символов и есть live-message — `edit_text` (с одним retry на FloodWait). Иначе — `send_long` режет на куски по границе HTML-тегов.

### 2.8. Очистка
`finally`-блок: остановка heartbeat и typing, удаление задачи из `_active_tasks`.

---

## 3. CLI-провайдеры (`providers/`)

Общий интерфейс — `CLIProvider` в `providers/base.py`:

```python
async def run(prompt, cwd, session_id, model, attachments, progress) → (text, new_session_id, meta)
```

`_exec` — общий subprocess-helper:
- На Windows `shutil.which` находит реальный путь к `.cmd`/`.bat` (npm-обёртки). Если расширение `.cmd`/`.bat` — запуск через `cmd /c <resolved>`, иначе напрямую (`asyncio.create_subprocess_exec` не умеет PATHEXT).
- Таймаут `CLI_TIMEOUT` (default 1800с).
- Логирование: команда, cwd, превью промпта (200 символов), длина stdin, rc, длины stdout/stderr.

### 3.1. `ClaudeCodeProvider`
- Подменяет `CLAUDE_CONFIG_DIR=<account.cli_home_path>` — изоляция между аккаунтами Claude.
- Запуск: `claude --print --output-format stream-json --verbose --include-partial-messages --permission-mode acceptEdits --append-system-prompt "..." [--model X] [--resume <session>]`.
- Промпт **передаётся через stdin**, а не как аргумент: на Windows `cmd.exe` режет командную строку на ~8191 символе и `claude` падает с «The command line is too long».
- Stream-JSON парсится построчно (`readline`, `limit=32 MiB` — один tool_use Read с большим файлом может быть огромным).
- Поддерживаемые типы событий:
  - `system/init` — берём `session_id`.
  - `assistant` — основной носитель текста; внутри `content[]` ищем `tool_use`-блоки и сохраняем их в `tool_call_log` (дедуп по `id`).
  - `stream_event` (`content_block_delta`, `content_block_start`, `content_block_stop`) — partial-чанки с `--include-partial-messages`.
  - `tool_use` (отдельным сообщением) — для Edit/Write/MultiEdit считаем `+added −removed` строк, обрезаем old/new до 2000 символов перед сохранением в `events.payload`.
  - `result` — финал, забираем `usage.input_tokens` / `output_tokens`.
- Cancellation: при `asyncio.CancelledError` — `proc.kill()` сразу (иначе claude-процесс становится фантомом и держит квоту).
- Опциональный дамп сырых событий в `.runtime/logs/claude-stream-*.jsonl` через `CLAUDE_DEBUG_STREAM=1`.

### 3.2. `CodexProvider`
- `CODEX_HOME=<cli_home>`.
- Запуск `codex exec [resume <sid>] --skip-git-repo-check [-c model=...] -c instructions=...`.
- Стриминга нет — `progress` игнорируется.
- `session_id` ищется в stdout/stderr эвристикой («строка содержит session и id, выделяем токен ≥16 символов с дефисом»).

### 3.3. `GeminiProvider`
- `USERPROFILE=<cli_home>` (Gemini хранит конфиг в `%USERPROFILE%`).
- Stateless `-p` вызов — нет нативной сессии.
- **Общая память с Claude**: перед каждым запросом ищет `cli_homes/claude_code__*/projects/<encoded-cwd>/memory/MEMORY.md`, склеивает с остальными `.md` той же папки и подмешивает в начало промпта (read-only для Gemini).

### 3.4. Реестр
`providers/__init__.py::REGISTRY` мапит `account.provider` → класс. `providers.make(account_row)` возвращает экземпляр.

---

## 4. БД (`core/db.py`)

SQLite, файл `bridge.sqlite3` в корне. Контекст-менеджер `db.conn()` отдаёт connection с `row_factory = sqlite3.Row`, коммитит на выходе.

Таблицы:
- **`users`** — telegram_id, role (`admin`/`user`), created_at.
- **`accounts`** — provider, label (UNIQUE), cli_home_path, default_model, enabled, notes. Один аккаунт = одна изолированная папка `.runtime/cli_homes/<provider>__<label>/`.
- **`conversations`** — `(chat_id, thread_id)` UNIQUE, account_id, model, **provider_session_id** (нативный id сессии CLI для `--resume`), cwd, project_name.
- **`messages`** — `(conversation_id, role, content, provider, model, created_at)`. Используется для контекста при смене провайдера (`build_prompt_with_history`).
- **`events`** — структурированный лог: `event_type` (`message_in`/`message_out`/`error`/...), tokens_in/out, duration_ms, payload (JSON, может содержать edits/tool_uses).

Миграции — простые, через `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` (см. `MIGRATIONS` в `db.py`).

При `db.init()`: создаётся схема, прогоняются миграции, и если `ADMIN_ID` есть — вставляется админская запись через `INSERT OR IGNORE`.

---

## 5. Память (Claude Code) и junction'ы

Claude Code ведёт свою auto-memory: `MEMORY.md` (индекс) + отдельные `.md` (user_*, feedback_*, project_*, reference_*) в `cli_homes/<account>/projects/<encoded-cwd>/memory/`. **`encoded-cwd`** — это путь с заменой `\` и `:` на `-` (например, `C--Users-Administrator`).

Проблема: каждый Claude-аккаунт ведёт свою отдельную папку памяти. Хочется одну общую.

Решение в `utils/memory_link.py::ensure_memory_links()` (запускается при старте бота):

1. Создаёт общую папку `HereAssistant/memory/`.
2. Для каждой `cli_homes/<account>/projects/<cwd>/memory/`:
   - Уже **NTFS junction** на общую → пропуск.
   - Обычная папка с файлами → файлы (которых ещё нет в общей) копируются в общую, исходная удаляется, на её месте создаётся junction (`cmd /c mklink /J ...`).
   - Папки нет → сразу junction.
3. Junction'ы не требуют админ-прав и для CLI выглядят как обычные папки — Claude пишет в `memory/`, а физически файлы лежат в `HereAssistant/memory/`.

Это работает только на Windows и только для NTFS. Если перенос на Linux — нужны симлинки и проверка прав.

---

## 6. Самоперезапуск (`/deploy` и `restart_watcher`)

Перезапуск — это «применить только что отредактированный код, не теряя текущий чат».

### Сценарий A: `/deploy`
1. `handlers/deploy.py` сохраняет `restart.json` (chat_id, thread_id, hash_before, text_before, reason).
2. `os.execv(sys.executable, [sys.executable, "bot.py"])` — текущий процесс замещается новым с тем же PID.
3. Новый процесс при старте читает `restart.json`, считает diff hash до/после, отчитывается «✅ Перезапущен. Изменения: +X −Y». См. `post_restart_report`.
4. После отчёта обновляется снимок проекта (`version.save_snapshot()`) для следующего сравнения.

### Сценарий B: отложенный перезапуск (после моих правок кода)
Запись `restart_request.json` с `{chat_id, thread_id, reason}` — фоновая таска `restart_watcher` (стартует в `bot.py::main`) каждые 2с проверяет:

- файл появился И нет активных задач в `_active_tasks` → шлёт «🔄 Перезапускаю — <reason>», пишет `restart.json` (для post-restart отчёта), удаляет request-файл, делает `os.execv`.

Я (Claude) использую этот механизм после правок кода — пишу `restart_request.json` и продолжаю работу, бот сам себя перезапустит, как только освободится.

---

## 7. Версионирование и diff (`core/version.py`)

Снимок проекта — это `{relative_path: {hash, text}}` для всех `.py` файлов из `_PROJECT_GLOBS` (исключая `__pycache__`, `.runtime`, `workspace`, `backups`).

Два файла:
- `.runtime/state/snapshot.json` — лёгкий (только hash + lines), для быстрых проверок.
- `.runtime/state/snapshot_full.json` — полный текст, для diff между перезапусками.

`project_changes(old_snap)` сравнивает старый снимок с текущим состоянием диска, считает `+added −removed` через `difflib.unified_diff`. Это и есть «что изменилось с прошлого старта», что показывается после `/deploy`.

Бэкапы: `backup_current_bot()` копирует `bot.py` в `.runtime/backups/bot-<ts>-<hash8>.py`, хранит последние `BACKUP_RETENTION_COUNT` (default 20).

---

## 8. Конфиг (`core/config.py`)

`.env` парсится самописным парсером (никаких зависимостей). Из него:

- `TELEGRAM_BOT_TOKEN` — токен.
- `ADMIN_TELEGRAM_ID` — если пусто или `PASTE_HERE`, бот стартует в **claim-mode**: генерирует `CLAIM_CODE` через `secrets.token_urlsafe(8)`, дописывает в `.env`, и первый человек, написавший `/start <CLAIM_CODE>`, становится админом.
- `DEFAULT_CWD` — рабочая папка по умолчанию (default `workspace/default/`).
- `CLI_TIMEOUT_SEC`, `MAX_HISTORY`, `LOG_RETENTION_DAYS`, `BACKUP_RETENTION_COUNT`.

`RU_SYSTEM_INSTRUCTION` — единый русский system prompt, который шлётся всем CLI (Claude через `--append-system-prompt`, Codex через `-c instructions=...`, Gemini — склейкой в начало промпта).

`append_env(key, value)` — атомарное обновление `.env`: читает, заменяет/добавляет строку, перезаписывает.

---

## 9. Markdown → Telegram HTML

`utils/markdown.py::markdown_to_html` — кастомный мини-парсер:
- ` ``` ` → `<pre><code>`;
- `` ` `` → `<code>`;
- `**` / `*` → `<b>` / `<i>`;
- `[text](url)` → `<a href>`;
- `> ` → `<blockquote>`;
- `#` `##` `###` → жирный.

HTML тэги ескейпятся внутри code/pre. Telegram parse_mode=HTML — потому что у MarkdownV2 слишком строгий ескейп специальных символов.

`send_long` (в `handlers/common.py`) режет длинные сообщения по границе тегов, не разрывая `<pre>` и `<code>` посередине.

---

## 10. Изоляция аккаунтов

Один Telegram-бот ↔ несколько CLI-аккаунтов (например, два Claude'а на разных подписках + Codex + Gemini). Изоляция через переменные окружения, подменяемые в `provider.env()`:

| Провайдер  | Переменная           | Что изолирует                |
|------------|----------------------|------------------------------|
| Claude     | `CLAUDE_CONFIG_DIR`  | сессии, ключи API, projects/ |
| Codex      | `CODEX_HOME`         | сессии, конфиг               |
| Gemini     | `USERPROFILE`        | OAuth-токен, кэш             |

Папка для каждого аккаунта: `.runtime/cli_homes/<provider>__<label>/`. Создаётся при `manage.py` → «Добавить аккаунт».

---

## 11. События и статистика

`core/events.py::log(event_type, **fields)` пишет в `events`. Поля: timestamp, user/chat/thread, account_label, provider, model, tokens_in/out, duration_ms, payload (JSON).

`handlers/stats.py` показывает агрегаты:
- `/stats` — последние 24ч (запросы, токены, средняя длительность по моделям).
- `/stats week` — последние 7 дней.
- `/stats all` — за всё время.
- `/log` — последние события (`/log error` — только ошибки).

---

## 12. Что важно помнить при правках

- **Не править bot.py напрямую без перезапуска** — `restart_request.json` + ждать, пока активные задачи завершатся.
- **Длинные ответы и сообщения с вложениями** требуют `send_long`, не `message.answer`.
- **Любой `subprocess_exec` на Windows** должен идти через `_exec` или повторять его трюк с `.cmd`/`.bat`.
- **Stream-JSON readline нужен `limit=32 MiB`** — иначе `LimitOverrunError` на больших tool_result.
- **При cancellation провайдер обязан `proc.kill()`** — иначе фантомный CLI продолжит жечь токены.
- **Память Claude — junction'ы.** Перед `rmtree` любой папки в `cli_homes/*/projects/*/memory/` сначала проверять `_is_junction`, иначе можно снести общую папку.
- **`bridge.sqlite3` блокируется на запись** — все хендлеры используют `db.conn()` короткими блоками, не держат соединение во время CLI-вызова.

---

## 13. Граничные сценарии

- **Бот стартует, нет админа** — claim-режим, генерирует `CLAIM_CODE`, печатает в консоль ссылку `https://t.me/<bot>?start=<code>`.
- **Telegram FloodWait во время стриминга** — `progress` ставит `cooldown_until = now + retry_after + 1`, edit'ы паузятся.
- **CLI упал (`rc != 0`)** — `RuntimeError("claude failed (rc=...): <stderr first 2000>")`, ловится в `_process_message`, пишется в `events("error")`, в Telegram уходит «❌ Ошибка: <Type>: ...».
- **Промпт > 8 KiB** — Claude получает через stdin (а не как аргумент), Codex/Gemini пока через argv (в будущем — тоже stdin, если упрутся).
- **Два сообщения подряд за <1.5с** — склеиваются в одно через `_pending`. Если первое — текст, второе — голос, оба попадают в один промпт (голос предварительно расшифровывается Whisper'ом).

---

## 14. Что НЕ описано здесь

- Конкретные команды `/accounts`, `/model`, `/project` — см. `README.md` и сами `handlers/*.py`, они линейные.
- Установка и логин в CLI — `manage.py`, меню там самоописательное.
- Хронология фичей и ТЗ на будущее — `TZ.md`.
