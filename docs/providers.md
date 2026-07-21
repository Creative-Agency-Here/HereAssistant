# Провайдеры: Claude Code / Codex / Gemini CLI / Qwen Code

HereAssistant запускает агентов как **CLI-subprocess'ы** (не API): ты платишь
подпиской или coding-планом провайдера, а не прямым Python API, и получаешь
полноценные агентные возможности CLI.

## Установка CLI

```bash
# Claude Code
npm i -g @anthropic-ai/claude-code

# Codex CLI (OpenAI)
npm i -g @openai/codex

# Gemini CLI
npm i -g @google/gemini-cli

# Qwen Code (Node.js 22+)
npm i -g @qwen-code/qwen-code@latest
```

Достаточно любого одного. Проверка: `bash scripts/check_runtime.sh`.

## Изоляция аккаунтов (auth homes)

Каждый добавленный в бота аккаунт живёт в собственном каталоге
`.runtime/cli_homes/<provider>__<label>/` — провайдеру подсовывается свой
`CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `HOME` / `QWEN_HOME`, поэтому:

- можно держать несколько подписок одного провайдера и переключаться между ними;
- логин одного аккаунта не затирает другой;
- креды не смешиваются с твоим личным `~/.claude` / `~/.codex`.

Auth-home сам по себе не выдаёт доступ пользователю. Аккаунт должен иметь
`owner_user_id` либо явный `shared=1`; `owner_user_id=NULL` означает
«не назначен», а не «общий». Настройка выполняется через `manage.py`.

Если на сервере установлен RTK, Claude-профиль получает native PreToolUse hook
и точечные разрешения только для read/test-команд. Статистика хранится отдельно
в `<cli_home>/.rtk/history.db` через `RTK_DB_PATH`; телеметрия отключена.
После provider run HereAssistant удаляет command arguments, project paths и raw
tee-файлы, а `/rtk` и WebApp показывают только агрегаты владельца аккаунта.

**Эти каталоги содержат OAuth-токены подписок** — не коммить, не копировать в
чужие руки (уже в `.gitignore`; см. SECURITY.md).

## Авторизация аккаунта

Первый логин интерактивен (ссылка + код) — удобнее сделать один раз в терминале
сервера под нужным auth-home, например для Claude:

```bash
CLAUDE_CONFIG_DIR=$PWD/.runtime/cli_homes/claude_code__main claude
# внутри: /login → ссылка → код
```

Дальше аккаунт добавляется в бота (метка = имя каталога) и работает headless.

### Qwen Code: Token Plan и Coding Plan

В `manage.py` выбери `Qwen Code`, затем в открывшемся TUI выполни `/auth`:

1. `Alibaba ModelStudio`;
2. `Token Plan` для командного Token Plan Pro либо `Coding Plan` для персонального тарифа;
3. международный регион и plan-specific ключ формата `sk-sp-…`;
4. `/exit` после успешной проверки.

Ключ сохраняется только в gitignored auth-home
`.runtime/cli_homes/qwen_code__<label>/.qwen/`. Не записывай его в `.env`, Git,
Telegram или документацию. HereAssistant передаёт Qwen отдельные `QWEN_HOME` и
`QWEN_RUNTIME_DIR`, поэтому настройки и история не смешиваются с пользовательским
`~/.qwen` и другими аккаунтами.
Наследуемые из service-процесса provider API-переменные удаляются, а телеметрия
Qwen отключается, чтобы глобальный ключ не мог незаметно подменить выбранный
аккаунт.

Адрес выбирает сам Qwen Code по типу плана. Для диагностики: международный
Token Plan использует
`https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`, а
Coding Plan — `https://coding-intl.dashscope.aliyuncs.com/v1`. Менять Gemini CLI
для этого не требуется.

Дефолтная модель `qwen3.7-plus` поддерживается обоими планами. Доступные модели
переключаются командой `/model`; имя должно точно совпадать со списком тарифа.
`qwen3.8-max-preview` в текущем официальном allowlist отсутствует.

Qwen запускается в approval mode `auto`: безопасные операции оценивает встроенный
классификатор, а рискованные блокируются. Допустимые переопределения:
`QWEN_APPROVAL_MODE=plan|default|auto-edit|auto`; `yolo` намеренно запрещён.

## Резюме сессий

`claude` и `qwen` умеют `--resume <session_id>` — бот хранит `provider_session_id` в
диалоге и продолжает нативную сессию. Codex/Gemini продолжаются через краткий
контекст из локальной истории (если политика проекта разрешает её хранить —
см. `docs/privacy.md`; для private-проектов контекст держит только сам CLI).

## Полезные env

| Переменная | Смысл |
|---|---|
| `CLI_TIMEOUT_SEC` | лимит одного запроса (дефолт 1800с) |
| `CLAUDE_PERMISSION_MODE` | `acceptEdits` (дефолт) / `default`; `bypassPermissions` запрещён |
| `CLAUDE_DEBUG_STREAM`, `GEMINI_DEBUG_STREAM`, `QWEN_DEBUG_STREAM` | дамп сырого stream-json в логи |
| `QWEN_APPROVAL_MODE` | `auto` (дефолт) / `auto-edit` / `default` / `plan`; `yolo` запрещён |
