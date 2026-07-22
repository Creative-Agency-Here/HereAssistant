# HereAssistant

🌐 **[hereassistant.hereagency.ru](https://hereassistant.hereagency.ru)** · 🇬🇧 [English version](README.md)

Личный Telegram-бот → несколько CLI-ассистентов (Claude Code, Codex, Gemini, Qwen Code). Явные владельцы/shared-аккаунты, изоляция проектов, переключение моделей, статистика, самоперезапуск. Работает на подписках и coding-планах провайдеров.

Первый экран предлагает режим работы кнопками, Terminal CLI меняет заголовок по
текущей задаче, а Web App сводит локальный и серверный контуры, Git, диск и
подтверждённое состояние деплоя.

Нативное расширение VS Code открывает задачи отдельными terminal-editor вкладками
с живыми названиями, добавляет быстрое меню в status bar и блок Git/деплоя в
стандартную вкладку Source Control.

**Privacy-first:** по умолчанию каждый проект `private` — содержимое сообщений и диффы не сохраняются, во внешние системы (CRM) ничего не уходит. Ослабляется только явным `.hereassistant/project.yml` в конкретном проекте — см. [docs/privacy.md](docs/privacy.md).

Метрики использования различают модель, аккаунт и нормализованную среду запуска
(Telegram, HereAssistant CLI/VS Code и приложение терминала из закрытого списка),
но не сохраняют команды, заголовки окон и содержимое диалогов.

Нативные сессии Claude Code, Codex, Qwen Code и Gemini CLI можно
подключить к HereCRM через один privacy-gated коннектор HereAssistant.
Инструкция для своей машины и машин сотрудников:
[docs/native-session-connector.ru.md](docs/native-session-connector.ru.md).

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

Меню: **2** — добавить аккаунт и залогинить провайдера (Claude/Codex/Gemini/Qwen),
**1** — показать аккаунты, **9 → 5** — открыть `.env`, **9 → 8** —
настроить native AI-сессии, **8** — запустить бота.

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
командой: `hereassistant`.

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
│   ├── gemini.py
│   └── qwen_code.py
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
- `/rtk` — сколько контекстных токенов сэкономил RTK
- `/project clone|worktree|status|pull|push` — отдельные репозитории и ветки пользователя; push только после подтверждения
- `/model` — модель
- `/cwd`, `/where`, `/project list|new|use` — рабочая папка
- `/new`, `/reset` — сессия и история
- `/status`, `/version` — задачи, Git, диск, деплой и диагностика
- `/memory` — статус общей памяти Claude/Codex текущего проекта
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

## Мобильный Mini App

Nuxt 3 Mini App открывается внутри Telegram или в браузере и авторизуется через
Telegram `initData`. Раздел «Активность» объединяет личные сессии HereCRM,
CLI/Telegram-каналы, недельные и 30-дневные отчёты. CRM-данные видит только
владелец ассистента.

- **Сейчас** — живая задача, текущий шаг, действия и потоковый лог.
- **Активность** — личные сессии, свёрнутые tool-события с раскрытием деталей и отчёты.
- **Правки** — журнал изменений по файлам с полными diff.
- **Подключения** — состояния локального/серверного контуров, Git-расхождения, диск и подтверждение деплоя; credentials, remotes, имена изменённых файлов и auth-home пути API не возвращает.

Terminal CLI запускается через `.venv/bin/python chat.py`. В заголовке окна он
показывает название и количество задач, анимацию во время выполнения и `✕`, пока
работа явно не завершена. `/tasks` показывает открытые HereCRM-задачи выбранного
CRM-проекта, `/status` — Git push/pull и deploy-marker без догадок. Символ `/`
открывает фильтруемый каталог команд с выбором через Tab, Enter или мышь.
`/permissions` переключает Codex между профилем аккаунта, read-only и sandbox с
записью внутри workspace; запрещённые операции завершаются безопасной ошибкой без
ложной имитации покомандного окна одобрения неинтерактивного `codex exec`.

### Проверенные подробности действий

Вызовы Read, Edit, Write, Bash и Agent раскрываются в структурированные мобильные карточки: содержимое файлов, визуальный diff, точная команда, ограниченный вывод, статус, длительность и токены, когда они доступны. Pull-up bottom sheet и все пять режимов зафиксированы автотестами и настоящими скриншотами 390 × 844.

<table>
  <tr>
    <td><img src="docs/img/activity/activity-read.png" alt="Подробности Read" width="250"></td>
    <td><img src="docs/img/activity/activity-edit.png" alt="Подробности Edit" width="250"></td>
    <td><img src="docs/img/activity/activity-bash.png" alt="Подробности Bash" width="250"></td>
  </tr>
</table>

Полная проверенная галерея и воспроизводимые команды: [docs/mobile-activity-proof.ru.md](docs/mobile-activity-proof.ru.md).

## VS Code Workbench

<table>
  <tr>
    <td><img src="docs/img/vscode-workbench-terminal.png" alt="HereAssistant выполняет задачу в терминале VS Code" width="560"></td>
    <td><img src="docs/img/vscode-workbench-actions.png" alt="Быстрые действия HereAssistant в VS Code" width="560"></td>
  </tr>
</table>

Установка HereAssistant и расширения на macOS:

```bash
git clone https://github.com/Creative-Agency-Here/HereAssistant.git
cd HereAssistant
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3 scripts/package_vscode_extension.py
code --install-extension dist/hereassistant-vscode-0.7.5.vsix --force
```

Перезапустите VS Code, нажмите фиолетовый **Here** в status bar, выберите
`Настроить подключение` и укажите клонированную папку. Быстрое меню запускает и
возвращает терминальные сессии, открывает HereCRM и управляет AI-аккаунтами.
Чтобы остановить конкретный ответ, выберите его terminal и используйте обычный
`Ctrl+C` самого терминала.

Расширение запускает существующий `chat.py` в текущем workspace, открывает каждую
задачу отдельной вкладкой с динамическим названием, показывает CRM-задачи и
контуры Mac/сервера, а Pull/Push/подтверждённый деплой оставляет в Source Control.

Полная инструкция: [docs/vscode-workbench.ru.md](docs/vscode-workbench.ru.md).

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

## Общая память CLI-агентов

Для проекта с `agent.profile: unified` и явным `agent.memory.enabled: true` Claude, Codex и
другие CLI получают одну owner/project-scoped Markdown-память. HereAssistant всегда добавляет
`MEMORY.md`, выбирает релевантные тематические заметки и не отправляет память в CRM. Native
Claude memory можно безопасно импортировать и связать с общей папкой; Codex читает тот же
контекст через gateway. Подробный контракт и rollout:

[Единый runtime агентов](docs/unified-agent-runtime.ru.md)

## Где физически лежат данные

Каждый аккаунт = отдельная папка `.runtime/cli_homes/<provider>__<label>/`. Бот подменяет `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `HOME` перед запуском CLI. Эти папки содержат OAuth-креды подписок — см. [SECURITY.md](SECURITY.md).

## Безопасность

- Один админ — назначается через claim при первом запуске (`ADMIN_IDS` в `.env`).
- Бот = удалённое управление машиной. Полная модель угроз — [SECURITY.md](SECURITY.md).
- Использование подписок через бота — серая зона ToS провайдеров. Для личного использования одним человеком — обычно ок; решение и риск за тобой.

## Лицензия

[MIT](LICENSE)
