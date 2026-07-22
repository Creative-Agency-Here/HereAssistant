# Native CLI-сессии → HereAssistant → HereCRM

Единый коннектор фиксирует прямые сессии Claude Code, Codex, Qwen Code и Gemini CLI
через те же privacy-гейты и надёжный outbox, что и диалоги, запущенные из
HereAssistant. Он не копирует provider credentials, не заменяет project lifecycle
hooks и не включает глобальное отслеживание.

## Граница приватности

- Нет ближайшего `.hereassistant/project.yml` — папка `private`, событие не попадает в outbox.
- Политика родителя работает для вложенных папок. Вложенный `mode: private` её перекрывает.
- `local` никогда не попадает в HereCRM.
- `crm` требует `sync.enabled: true` и CRM project/task ID.
- Transcript не читается, пока явно не включён `send_prompts` или `send_messages`.
- Путь transcript принимается только внутри штатной auth/session home соответствующего CLI.

## Настройка коннектора

В локальном `.env` HereAssistant нужны штатные scoped-параметры:

```dotenv
HERECRM_SYNC_URL=https://crm-api.example.com/api/v1
HERECRM_SYNC_TOKEN=has_COPY_ONCE
HEREASSISTANT_NATIVE_USER_ID=123456789
HERECRM_SYNC_ORIGIN=employee-laptop
```

Токену нужен scope `sessions:write`; `sessions:read` оставь, если эта же инсталляция читает
личную витрину активности. Не отправляй токен в чат и не коммить `.env`.

Открой единый manager:

```bash
.venv/bin/python manage.py
```

Выбери **Настройки → AI-сессии → HereCRM**. Экран покажет коннектор, идентичность,
outbox и все четыре CLI. Отсюда можно установить/обновить hooks, настроить папку
или удалить только hooks HereAssistant.

Неинтерактивные команды:

```bash
.venv/bin/python scripts/native_connector.py status
.venv/bin/python scripts/native_connector.py install --clients all
.venv/bin/python scripts/native_connector.py uninstall --clients all
```

Установка сохраняет чужие hooks, делает private backup в `~/.hereassistant/hook-backups/` и
идемпотентна. Hook ссылается на текущие checkout HereAssistant и Python; после переноса
папки или пересоздания venv запусти install ещё раз.

## Выбор папок

Manager безопасно создаёт или обновляет `<project>/.hereassistant/project.yml`.
Для начала рекомендуется только видимость метаданных:

```yaml
name: "Example project"
mode: crm
crm_project_id: "CRM_PROJECT_UUID"
sync:
  enabled: true
  send_prompts: false
  send_messages: false
  send_diffs: false
  send_commits: false
  send_deploys: false
  send_artifacts: false
```

В этом файле нет credentials. Его можно делить с командой, если CRM ID одинаковы для всех.
Для исключённого subtree создай внутри него локальный `mode: private`.

## Раскатка сотрудникам

Каждый сотрудник ставит HereAssistant и hooks на свой компьютер. Входы в CLI остаются
локальными. Каждой инсталляции выдай свой scoped-токен, native user ID и origin; не копируй
сотруднику чужой `.env`.

После установки:

1. Перезапусти открытые CLI-сессии. В Codex проверь `/hooks`; где CLI требует trust папки — подтверди его.
2. Запусти короткую сесию из папки, которая явно настроена как `crm`.
3. Запусти `native_connector.py status`: после доставки outbox должен вернуться к нулю.
4. В HereCRM проверь владельца, провайдер, модель, terminal surface и проект.
5. Повтори тест в ненастроенной папке и убедись, что сессия в CRM не появилась.

Если раньше был установлен отдельный direct-sync hook, удали его штатным старым
инсталлером только после проверки нового. HereAssistant намеренно не удаляет чужие hooks.
