# HereAssistant

Личный Telegram-бот → несколько CLI-ассистентов (Claude Code, Codex, Gemini). Один админ, изоляция аккаунтов, переключение моделей, статистика, самоперезапуск.

**Privacy-first:** по умолчанию каждый проект `private` — содержимое сообщений и диффы не сохраняются, во внешние системы (CRM) ничего не уходит. Ослабляется только явным `.hereassistant/project.yml` в конкретном проекте — см. [docs/privacy.md](docs/privacy.md).

## Быстрый старт (Ubuntu, production)

```bash
git clone <repo-url> hereassistant && cd hereassistant
bash scripts/bootstrap_ubuntu.sh     # venv + зависимости + сборка фронта + .env
# заполнить .env, залогинить CLI (docs/providers.md), затем:
pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api
```

Полный runbook (nginx, HTTPS, автозапуск): [docs/ubuntu-pm2-nginx.md](docs/ubuntu-pm2-nginx.md).
Провайдеры и auth-homes: [docs/providers.md](docs/providers.md). Модель угроз: [SECURITY.md](SECURITY.md). Как контрибьютить: [CONTRIBUTING.md](CONTRIBUTING.md).
Windows-запуск (`start_bot.bat`, `node.exe`) поддерживается как legacy.

## Архитектура

```
HereAssistant/
├── bot.py              # точка входа (тонкая)
├── manage.py           # CLI-меню для регистрации аккаунтов
├── requirements.txt
├── README.md
├── TZ.md               # ТЗ для дальнейшего развития (хронология итераций)
├── .env                # токен, admin_id
├── bridge.sqlite3      # БД (создаётся при первом запуске)
│
├── core/               # фундамент — не зависит от Telegram
│   ├── config.py       # загрузка .env, пути, константы
│   ├── db.py           # схема SQLite, миграции
│   ├── logging_setup.py# ротация логов по дням
│   ├── events.py       # запись/чтение таблицы events
│   └── version.py      # хеш bot.py, diff, бэкапы
│
├── providers/          # обёртки над CLI
│   ├── base.py         # CLIProvider, _exec (с фиксом .cmd для Windows)
│   ├── claude_code.py
│   ├── codex.py
│   └── gemini.py
│
├── handlers/           # Telegram-роутеры
│   ├── admin_claim.py  # /start + claim
│   ├── messages.py     # обычные сообщения + файлы
│   ├── accounts.py     # /accounts, /account (inline-кнопки)
│   ├── models.py       # /model (inline-кнопки)
│   ├── projects.py     # /cwd, /where, /project
│   ├── system.py       # /status, /version, /help, /new, /reset
│   ├── stats.py        # /stats, /log
│   ├── deploy.py       # /deploy + post-restart отчёт
│   ├── common.py       # is_admin, send_long
│   └── repo.py         # операции с БД
│
├── utils/
│   ├── markdown.py     # to_telegram_md (clean/escaped/off)
│   ├── files.py        # скачивание Telegram-вложений
│   └── locks.py        # per-thread asyncio.Lock
│
├── workspace/          # рабочие папки для CLI (создаётся при старте)
│   └── default/        # cwd по умолчанию
│
└── .runtime/
    ├── cli_homes/      # изолированные папки аккаунтов
    ├── downloads/      # скачанные из Telegram файлы
    ├── logs/           # bot.log + bot.log.YYYY-MM-DD
    ├── backups/        # резервные копии bot.py перед /deploy
    └── state/restart.json  # состояние при /deploy
```

## Быстрый старт

См. подробно — в TZ.md. Кратко:

```cmd
cd C:\Users\Administrator\Desktop\HereAssistant
python manage.py
```

Меню:
- **6** — поставить зависимости (npm + pip)
- **7** — открыть `.env`, вписать `TELEGRAM_BOT_TOKEN` (новый, после revoke у BotFather). `ADMIN_TELEGRAM_ID` оставить пустым — заполнится через claim.
- **2** — добавить аккаунт + залогиниться
- **8** — запустить бота

В консоли появится claim-ссылка → открой в Telegram → Start → ты админ.

## Команды Telegram

- `/help` — справка
- `/accounts`, `/account use X` — аккаунты
- `/model` — модель
- `/cwd`, `/where`, `/project list|new|use` — рабочая папка
- `/new`, `/reset` — сессия и история
- `/status`, `/version` — диагностика
- `/stats`, `/stats week`, `/stats all` — статистика
- `/log`, `/log error` — события
- `/deploy` — перезапустить процесс с применением изменений

## Процессы и запуск (PM2)

Все три процесса живут под PM2 нативно на Windows (без Docker/WSL). Конфиг — `ecosystem.config.js` в корне проекта.

| Процесс | Что | Скрипт | Порт |
|---|---|---|---|
| `here-assistant-bot` | сам бот | `bot.py` (Python) | — |
| `here-assistant-api` | бэкенд webapp | `webapp/api/server.py` (aiohttp) | 8200 |
| `here-assistant-front` | фронт webapp | Nuxt dev (`node.exe`) | 3000 |

Команды:

```cmd
pm2 start ecosystem.config.js     # поднять всё разом
pm2 restart here-assistant-bot    # перезапустить только бота
pm2 logs here-assistant-bot       # живые логи бота
pm2 list                          # что запущено + аптайм + рестарты
pm2 save                          # запомнить набор процессов (автостарт после ребута)
```

Нюансы:
- **`front` запущен через `node.exe` + `interpreter: 'none'`** — на Windows PM2 в fork-режиме неправильно собирает `.mjs`, поэтому Nuxt стартует прямым вызовом `node_modules/nuxt/bin/nuxt.mjs`.
- **Single-instance lock** — бот пишет PID в `.runtime/state/bot.lock` и при старте проверяет, не запущен ли уже другой живой экземпляр (`utils/single_instance.py`). Если запущен — второй стартующий печатает баннер «Бот уже запущен» и выходит с кодом 2. Это защищает от дублей при гонке `pm2 restart` + ручного запуска.
- **`/deploy` (самоперезапуск через `os.execv`)** ждёт «тишины» (когда нет активных задач) перед перезапуском, чтобы не оборвать ответ на полуслове, и после старта присылает «✓ Бот запущен» / отчёт с diff.
- **Альтернатива для отладки** — `start_bot.bat` запускает `bot.py` в видимой консоли (без PM2), удобно чтобы видеть трейсбеки вживую.

## Модели — добавление и актуализация

Модели хранятся в **трёх местах** (не путать):

1. **Список кнопок** — хардкод `POPULAR_MODELS` в `handlers/models.py`. Это меню, которое показывается при `/model` без аргумента.
2. **`accounts.default_model`** (БД) — модель по умолчанию для **новых** диалогов этого аккаунта.
3. **`conversations.model`** (БД) — что **реально активно** в конкретном треде прямо сейчас.

**`/model` не валидирует список** — команда с аргументом принимает любую строку. Это ключевой момент для свежих версий.

### Вышла новая версия модели — что делать

**Самое быстрое (в любом треде, без рестарта):**
```
/model claude-opus-4-9
```
Команда примет строку, перепишет `conversations.model`, сбросит сессию — следующее сообщение пойдёт уже на новой модели. Если модель не существует — бот не упадёт, но следующий вызов CLI вернёт ошибку «model not found» текстом.

**Чтобы появилась в кнопках `/model`** — добавь первой строкой в `handlers/models.py`:
```python
POPULAR_MODELS = {
    "claude_code": [
        "claude-opus-4-9",   # ← новая, первой
        "claude-opus-4-8",
        ...
    ],
    ...
}
```
Применится после `pm2 restart here-assistant-bot` (или `/deploy`).

**Чтобы стала дефолтом для всех новых диалогов** — обнови `accounts.default_model` в БД:
```sql
UPDATE accounts SET default_model='claude-opus-4-9' WHERE label='claude_hus';
```
(`bridge.sqlite3` в корне). После этого новые треды и `/new` стартуют на новой модели; существующие — переключаются через `/model`.

## Главные новые возможности (v0.9)

1. **Модульная архитектура** — bot.py теперь точка входа, основная логика в `core/`, `providers/`, `handlers/`, `utils/`
2. **Ротация логов по дням** — `bot.log` + `bot.log.2026-05-24` и т.д., хранятся 30 дней
3. **Таблица `events`** — структурированный лог: тип события, токены, длительность, payload
4. **Команды `/stats` и `/log`** — статистика использования по моделям, ошибки
5. **Русский system prompt** — все три CLI инструктируются отвечать по-русски. У Claude и Codex это настоящий system prompt (`--append-system-prompt`, `instructions=`), у Gemini в non-interactive `-p` такого флага нет — инструкция склеивается в начало пользовательского промпта вместе с памятью (см. ниже).
6. **Markdown → Telegram HTML** — все ответы рендерятся в HTML (`<b>`, `<i>`, `<code>`, `<pre>`, `<a>`, `<blockquote>`), цитаты и форматирование работают
7. **Inline-кнопки** — `/accounts`, `/model`, `/reset` показывают кнопки выбора
8. **`/help` и автоподсказки `/`** — через `setMyCommands`
9. **workspace/** — `cwd` по умолчанию = `workspace/default/`, команды `/project new|use|list` для создания подпапок-проектов
10. **`/deploy`** — самоперезапуск через `os.execv`, отчёт с diff после старта
11. **`/version`** — короткий хеш bot.py + дата
12. **Резервные копии** — перед каждым `/deploy` копия `bot.py` в `.runtime/backups/`, хранятся последние 20
13. **Приём файлов** — документы/фото/аудио/voice/видео скачиваются в `.runtime/downloads/`, путь передаётся CLI
14. **Общая память для Gemini** — Gemini читает память, накопленную Claude в том же cwd (см. ниже)

## Общая память между Claude и Gemini

У Claude Code есть собственная file-based память: `MEMORY.md` (индекс) + отдельные `.md` файлы (user_, feedback_, project_, reference_) в `cli_homes/claude_code__<label>/projects/<encoded-cwd>/memory/`. Её ведёт сам Claude.

Чтобы Gemini не работал «вслепую», `providers/gemini.py` перед каждым запросом:

1. Кодирует текущий cwd в формат Claude (`C:\X` → `C--X`).
2. Ищет в `cli_homes/claude_code__*/projects/<encoded>/memory/MEMORY.md` — берёт первый найденный.
3. Склеивает `MEMORY.md` + все остальные `.md` из этой папки в один текст.
4. Подмешивает в начало промпта Gemini вместе с `RU_SYSTEM_INSTRUCTION` и пометкой «память только для чтения».

Эффект:
- Память едина, не дублируется и не требует синхронизации — Gemini всегда видит актуальный snapshot.
- Если у Claude памяти для этого cwd нет — Gemini работает как раньше, без памяти.
- Gemini не может *менять* память (он stateless `-p`-вызов). Запись остаётся за Claude.

Включается автоматически — никакой конфигурации не требуется.

## Где физически лежат данные

См. подробно в TZ.md. Кратко: каждый аккаунт = отдельная папка `.runtime/cli_homes/<provider>__<label>/`. Бот подменяет `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `USERPROFILE` перед запуском CLI.

## Безопасность

- Один админ — назначается через claim при первом запуске.
- Бот = удалённое управление машиной. Не отправляй пароли и приватные документы.
- Использование подписок через бота — серая зона ToS. Для одного человека редко — обычно ок.

## Дальнейшее развитие

См. `TZ.md` — там список приоритетных задач и хронология итераций.
