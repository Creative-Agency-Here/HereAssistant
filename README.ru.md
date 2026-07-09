# HereAssistant

🇬🇧 [English version](README.md)

Личный Telegram-бот → несколько CLI-ассистентов (Claude Code, Codex, Gemini). Один админ, изоляция аккаунтов, переключение моделей, статистика, самоперезапуск. Работает на твоих подписках (без потокенной оплаты API).

**Privacy-first:** по умолчанию каждый проект `private` — содержимое сообщений и диффы не сохраняются, во внешние системы (CRM) ничего не уходит. Ослабляется только явным `.hereassistant/project.yml` в конкретном проекте — см. [docs/privacy.md](docs/privacy.md).

## Быстрый старт (Ubuntu, production)

```bash
git clone https://github.com/Creative-Agency-Here/HereAssistant.git && cd HereAssistant
bash scripts/bootstrap_ubuntu.sh     # venv + зависимости + сборка фронта + .env
# заполнить .env, залогинить CLI (docs/providers.md), затем:
pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api
```

Полный runbook (nginx, HTTPS, автозапуск): [docs/ubuntu-pm2-nginx.md](docs/ubuntu-pm2-nginx.md).
Провайдеры и auth-homes: [docs/providers.md](docs/providers.md). Модель угроз: [SECURITY.md](SECURITY.md). Как контрибьютить: [CONTRIBUTING.md](CONTRIBUTING.md).
Windows-запуск (`start_bot.bat`) поддерживается как legacy.

## Меню управления (manage.py)

Управление ботом — интерактивное меню `manage.py` (аккаунты, логин провайдеров,
`.env`, зависимости, запуск). На сервере запускается из корня проекта:

```bash
python3 manage.py
```

Меню: **2** — добавить аккаунт и залогинить провайдера (Claude/Codex/Gemini),
**1** — показать аккаунты, **7** — открыть `.env`, **8** — запустить бота.

Если бот развёрнут на удалённом сервере под пользователем `here` в
`/opt/hereassistant`, удобно завести на своей машине одну команду
(`~/.zshrc` / `~/.bashrc`):

```bash
hereassistant() {
  ssh -t <ssh-хост> 'TERM=xterm-256color sudo -u here -i bash -lc \
    "cd /opt/hereassistant && python3 manage.py"'
}
```

После `source ~/.zshrc` (или нового окна терминала) меню открывается одной
командой: `hereassistant`. `TERM=xterm-256color` убирает предупреждение о
неизвестном типе терминала. Для пиксельного логотипа в терминалах с графикой
(Ghostty/Kitty/iTerm2) запусти без подмены TERM или с `HEREASSISTANT_LOGO=image`.

## Архитектура

```
HereAssistant/
├── bot.py              # точка входа (тонкая)
├── manage.py           # CLI-меню для регистрации аккаунтов
├── requirements.txt
├── .env                # токен, admin_id (не коммитится)
├── bridge.sqlite3      # БД (создаётся при первом запуске)
│
├── core/               # фундамент — не зависит от Telegram
│   ├── config.py       # загрузка .env, пути, константы
│   ├── db.py           # схема SQLite, миграции
│   ├── project_config.py # privacy-политика проектов (.hereassistant/project.yml)
│   ├── logging_setup.py# ротация логов по дням
│   ├── events.py       # запись/чтение таблицы events
│   ├── changes.py      # журнал диффов правок
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
│   ├── messages.py     # обычные сообщения + файлы (+ privacy-гейты)
│   ├── accounts.py     # /accounts, /account (inline-кнопки)
│   ├── models.py       # /model (inline-кнопки)
│   ├── projects.py     # /cwd, /where, /project
│   ├── system.py       # /status, /version, /help, /new, /reset
│   ├── stats.py        # /stats, /log
│   ├── deploy.py       # /deploy + post-restart отчёт
│   ├── common.py       # is_admin, send_long
│   └── repo.py         # операции с БД
│
├── webapp/
│   ├── api/            # aiohttp API (127.0.0.1:8200) + сервисный /api/v1
│   └── front/          # Nuxt 3 Mini App (в проде — статика через nginx)
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
    ├── cli_homes/      # изолированные папки аккаунтов (auth-файлы подписок!)
    ├── downloads/      # скачанные из Telegram файлы
    ├── logs/           # bot.log + журнал правок по дням
    ├── backups/        # резервные копии bot.py перед /deploy
    └── state/          # lock, restart.json
```

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
- `/web` — открыть Mini App

## Rich Messages (Bot API 10.1)

Финальные ответы уходят через `sendRichMessage` (поле `markdown`): нативные
таблицы, заголовки, код и математика — без PNG-костылей. Текст ответа стримится
анимируемым превью `sendRichMessageDraft` (только личка; троттлинг
`DRAFT_MIN_INTERVAL_SEC`). Любая ошибка API → автоматический откат на
классический HTML-путь (включая рендер таблиц картинками). Выключатели:
`RICH_MESSAGES=0`, `RICH_STREAM=0`. Реализация — `utils/rich.py` + гейты в
`handlers/messages.py`.

## Процессы и запуск (PM2)

Конфиг — `ecosystem.config.js` в корне проекта.

| Процесс | Что | Скрипт | Порт |
|---|---|---|---|
| `here-assistant-bot` | сам бот | `bot.py` (Python) | — |
| `here-assistant-api` | бэкенд webapp | `webapp/api/server.py` (aiohttp) | 8200 |
| `here-assistant-front-dev` | фронт, ТОЛЬКО dev (HMR) | Nuxt dev | 3000 |

В production фронт — статика `npm run generate`, которую отдаёт nginx; PM2-процесс фронта не нужен (см. [docs/ubuntu-pm2-nginx.md](docs/ubuntu-pm2-nginx.md)).

```bash
pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api
pm2 restart here-assistant-bot    # перезапустить только бота
pm2 logs here-assistant-bot       # живые логи
pm2 save                          # автостарт после ребута (+ pm2 startup)
```

Нюансы:
- **Single-instance lock** — бот пишет PID в `.runtime/state/bot.lock` и при старте проверяет, не запущен ли уже другой живой экземпляр (`utils/single_instance.py`). Защита от дублей при гонке `pm2 restart` + ручного запуска.
- **`/deploy` (самоперезапуск через `os.execv`)** ждёт «тишины» (нет активных задач) перед перезапуском и после старта присылает отчёт с diff.
- **Windows (legacy)** — `start_bot.bat` запускает `bot.py` в видимой консоли без PM2.

## Модели — добавление и актуализация

Модели хранятся в **трёх местах** (не путать):

1. **Список кнопок** — хардкод `POPULAR_MODELS` в `handlers/models.py` (меню `/model`).
2. **`accounts.default_model`** (БД) — модель по умолчанию для **новых** диалогов аккаунта.
3. **`conversations.model`** (БД) — что **реально активно** в конкретном треде.

**`/model` не валидирует список** — команда с аргументом принимает любую строку:

```
/model claude-opus-4-9
```

Команда перепишет `conversations.model` и сбросит сессию — следующее сообщение пойдёт на новой модели. Чтобы модель появилась в кнопках — добавь её в `POPULAR_MODELS` и перезапусти бота; чтобы стала дефолтом новых диалогов — обнови `accounts.default_model` в БД.

## Общая память между Claude и Gemini

У Claude Code есть file-based память (`MEMORY.md` + тематические `.md`) в auth-home аккаунта. Чтобы Gemini не работал «вслепую», `providers/gemini.py` перед запросом находит память Claude для текущего cwd и подмешивает её в начало промпта (read-only). Память едина и не требует синхронизации; запись остаётся за Claude. Включается автоматически.

## Где физически лежат данные

Каждый аккаунт = отдельная папка `.runtime/cli_homes/<provider>__<label>/`. Бот подменяет `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `HOME` перед запуском CLI. Эти папки содержат OAuth-креды подписок — см. [SECURITY.md](SECURITY.md).

## Безопасность

- Один админ — назначается через claim при первом запуске (`ADMIN_IDS` в `.env`).
- Бот = удалённое управление машиной. Полная модель угроз — [SECURITY.md](SECURITY.md).
- Использование подписок через бота — серая зона ToS провайдеров. Для личного использования одним человеком — обычно ок; решение и риск за тобой.

## Лицензия

[MIT](LICENSE)
