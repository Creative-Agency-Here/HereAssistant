# Единый runtime Claude, Codex и Qwen

## Цель

HereAssistant показывает один логический диалог, а Claude Code, Codex и Qwen остаются сменными
движками. Нативные session ID не смешиваются: при смене провайдера создаётся его собственная
сессия, а общий контекст формируется из разрешённой истории диалога, правил репозитория и
owner/project-scoped памяти.

## Единый конфиг

```yaml
mode: local
storage:
  save_history: true
  save_messages: true
  save_file_changes: true
agent:
  profile: unified
  memory:
    enabled: true
    max_items: 6
    max_context_chars: 12000
```

`storage.save_messages` нужен для handoff истории между провайдерами. Память включается
отдельно и никогда не ослабляет CRM privacy-гейты.

## Память

Источник истины — `<project>/.hereassistant/memory/*.md`. Каталог должен быть локально
исключён из Git. `MEMORY.md` служит коротким индексом, остальные файлы — тематическими
заметками. HereAssistant ограничивает размер контекста, не следует по symlink и пропускает
файлы с потенциальными секретами.

Импорт существующей Claude memory:

```bash
python3 scripts/import_claude_memory.py \
  --user-id TELEGRAM_USER_ID \
  --project-id HEREASSISTANT_PROJECT_ID \
  --source-dir /path/to/claude/project/memory \
  --copy-to-shared
```

Связь native Claude memory с общим каталогом:

```bash
python3 scripts/link_claude_memory.py \
  --user-id TELEGRAM_USER_ID \
  --project-id HEREASSISTANT_PROJECT_ID \
  --claude-home /path/to/claude/profile
```

Если native memory уже непустая, link-команда останавливается. Сначала выполни импорт; данные
не удаляются и не перезаписываются автоматически.

## Lifecycle hooks

Project hooks остаются рядом с кодом, потому что только репозиторий знает свои Git/deploy
правила. HereAssistant не копирует произвольные hook-команды в глобальный профиль.

- Codex читает версионируемый `.codex/hooks.json` из доверенного репозитория.
- Claude получает тот же функциональный набор из `.claude/hooks.template.json`, слитого в
  gitignored `.claude/settings.local.json`.
- Qwen читает версионируемый `.qwen/settings.json` после доверия папке через `/trust`; список
  активных событий проверяется через `/hooks list`.
- Direct Qwen синхронизирует native transcript на `Stop`; внутри HereAssistant этот путь
  отключается маркером runtime, и ту же историю единожды отправляет scoped outbox.
- Общие реализации account pinning, secret scan, CRM session task, Git ownership, session
  sync и handoff остаются в `scripts/hooks/` целевого репозитория.
- Auth-файлы и CRM-токены не копируются между Claude, Codex и пользователями.

Для Site/Service используется их канонический менеджер:

```bash
pnpm hooks:status
pnpm hooks:install
```

Codex требует одноразово открыть `/hooks` в каждом серверном clone и доверить прочитанные
определения. Для Qwen нужно подтвердить папку через `/trust`, затем проверить `/hooks list`.
Эти подтверждения нельзя обходить автоматической записью trust-state.
Если `.qwen/settings.json` подключает HereCRM MCP, личный `HERECRM_MCP_TOKEN` передаётся
процессу HereAssistant только через окружение; статус сервера проверяется через `/mcp`.

## Переключение провайдера

1. В Telegram выбери другой профиль через `/accounts`.
2. HereAssistant сбросит только native provider session ID.
3. Если `storage.save_messages: true`, новая модель получит разрешённую историю диалога.
4. Все три провайдера получат один индекс и релевантные файлы общей памяти.

Для `private` проекта без явных storage-флагов native session продолжает работать, но
межпровайдерный transcript-handoff намеренно отсутствует.
