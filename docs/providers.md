# Провайдеры: Claude Code / Codex / Gemini CLI

HereAssistant запускает агентов как **CLI-subprocess'ы** (не API): ты платишь
подпиской (Claude Pro/Max, ChatGPT Plus, Gemini), а не за токены, и получаешь
полноценные агентные возможности CLI.

## Установка CLI

```bash
# Claude Code
npm i -g @anthropic-ai/claude-code

# Codex CLI (OpenAI)
npm i -g @openai/codex

# Gemini CLI
npm i -g @google/gemini-cli
```

Достаточно любого одного. Проверка: `bash scripts/check_runtime.sh`.

## Изоляция аккаунтов (auth homes)

Каждый добавленный в бота аккаунт живёт в собственном каталоге
`.runtime/cli_homes/<provider>__<label>/` — провайдеру подсовывается свой
`CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `HOME`, поэтому:

- можно держать несколько подписок одного провайдера и переключаться между ними;
- логин одного аккаунта не затирает другой;
- креды не смешиваются с твоим личным `~/.claude` / `~/.codex`.

Auth-home сам по себе не выдаёт доступ пользователю. Аккаунт должен иметь
`owner_user_id` либо явный `shared=1`; `owner_user_id=NULL` означает
«не назначен», а не «общий». Настройка выполняется через `manage.py`.

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

## Резюме сессий

`claude` умеет `--resume <session_id>` — бот хранит `provider_session_id` в
диалоге и продолжает нативную сессию. Codex/Gemini продолжаются через краткий
контекст из локальной истории (если политика проекта разрешает её хранить —
см. `docs/privacy.md`; для private-проектов контекст держит только сам CLI).

## Полезные env

| Переменная | Смысл |
|---|---|
| `CLI_TIMEOUT_SEC` | лимит одного запроса (дефолт 1800с) |
| `CLAUDE_PERMISSION_MODE` | `acceptEdits` (дефолт) / `default`; `bypassPermissions` запрещён |
| `CLAUDE_DEBUG_STREAM`, `GEMINI_DEBUG_STREAM` | дамп сырого stream-json в логи |
