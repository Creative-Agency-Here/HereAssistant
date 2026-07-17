# Приватность: режимы проектов и `.hereassistant/project.yml`

Главный принцип HereAssistant: **default deny**. Проект без конфига — `private`:
содержимое сообщений, диффы и лог инструментов НЕ сохраняются, в CRM/внешние
системы НИЧЕГО не уходит. Глобального «разрешить всё» не существует —
приватность ослабляется только явным файлом в конкретном проекте.

Реализация: `core/project_config.py` (единственная точка решений) + гейты в
`handlers/messages.py` и `webapp/api/routes/tasks.py`.

## Как это работает

Перед каждой операцией сохранения бот читает
`<cwd проекта>/.hereassistant/project.yml`:

| Ситуация | Режим |
|---|---|
| файла нет | `private` |
| файл битый / не YAML / нет PyYAML | `private` (+ warning в лог, без содержимого файла) |
| `mode:` не из списка | `private` |

## Режимы

### `private` (дефолт)

- ничего не уходит наружу;
- содержимое сообщений/диффы не сохраняются в БД;
- в `events` пишутся только метрики: длины, токены, длительность, флаг `private`;
- нативная сессия провайдера (`--resume`) продолжает работать — контекст
  держит сам CLI, а не наша БД.

### `local`

- хранение локально — по явным `storage.*` флагам;
- CRM и сервисный API этот проект **не видят никогда**, независимо от флагов.

### `crm`

Включается только полным набором: `mode: crm` **и** `sync.enabled: true` **и**
`crm_project_id` (или `crm_task_id`). Каждый тип данных наружу — под отдельным
явным флагом.

Доставка выполняется через локальный outbox: разрешённый payload временно лежит
в `crm_sync_outbox` до подтверждённого ответа HereCRM и затем удаляется. Это
хранилище никогда не получает данные `private`/`local` проектов и независимо
применяет `send_prompts` к запросу пользователя и `send_messages` к ответу
ассистента. Сетевой сбой не задерживает Telegram-ответ и не приводит к дублям:
каждое событие имеет idempotency key.

Транспорт включается только парой `HERECRM_SYNC_URL` + `HERECRM_SYNC_TOKEN`.
Scoped-токен `has_…` привязан к конкретному CRM workspace, имеет отдельные права
`sessions:write` и `sessions:read`, хранится только в окружении процесса и может
быть немедленно отозван администратором пространства. Обратная витрина Mini App
проверяет владельца HereAssistant до сетевого запроса; CRM дополнительно
ограничивает conversations и feed actor-пользователем токена.

## Примеры

Приватный проект (эквивалентен отсутствию файла — писать не обязательно):

```yaml
name: "Private Project"
mode: "private"
sync:
  enabled: false
storage:
  save_history: false
  save_messages: false
  save_file_changes: false
```

Локальное хранение без CRM:

```yaml
name: "My Pet Project"
mode: "local"
storage:
  save_history: true
  save_messages: true
  save_file_changes: true
```

CRM-проект (метаданные — да, содержимое — нет):

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

## Гарантии сервисного API

- `SERVICE_API_TOKEN` аутентифицирует внешнюю систему, но **не обходит**
  политику: проекты `private`/`local` для `/api/v1/*` не существуют.
- Пустой `SERVICE_API_TOKEN` = сервисный API отключён (503), а не открыт.
- Владелец в WebApp (Telegram initData / `WEBAPP_ACCESS_KEY`) видит только то,
  что реально сохранено локально: у private-проектов история пуста by design.

## Что фиксируется всегда (метрики, не контент)

Факт запроса/ответа, длительность, токены in/out, модель/аккаунт, количество
правок. Этого достаточно для статистики нагрузки и лимитов, и это не содержит
текста, кода или путей файлов.
