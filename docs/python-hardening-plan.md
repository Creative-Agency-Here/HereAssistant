# Python hardening перед возможной миграцией на TypeScript

**Статус:** завершён
**Начато:** 2026-07-11
**Область:** Python-движок, Telegram-бот, Web API и CLI-провайдеры HereAssistant
**Не входит:** перенос на grammY/TypeScript, изменение продуктового поведения, деплой

## Цель

Зафиксировать текущее поведение HereAssistant автоматическими тестами, сделать
окружение воспроизводимым, усилить типизацию и только после этого безопасно
декомпозировать крупные модули. Получившийся набор тестов и fixtures должен стать
контрактом для будущего TypeScript-spike.

## Неприкосновенные инварианты

- Privacy остаётся default deny: отсутствующий, повреждённый или неполный
  `.hereassistant/project.yml` ничего не открывает.
- Сервисный токен не обходит project policy.
- SQLite остаётся основным локальным хранилищем, существующие БД обновляются без
  потери данных.
- Провайдеры остаются CLI-subprocess с раздельными auth-home.
- `bypassPermissions` не используется.
- Ubuntu — основной production-путь, Windows — поддерживаемый legacy-путь.
- В fixtures, логах, тестах и документации нет токенов, реальных промптов,
  приватных путей и внутренних адресов.

## Принятые технические решения

- Тесты: `pytest` + `pytest-asyncio` + `pytest-cov`.
- Линтер и форматирование: Ruff.
- Статическая типизация: Pyright. Включается постепенно: сначала `basic`, затем
  `strict` по критическим модулям.
- Воспроизводимые зависимости: `uv.lock`; `requirements.txt` сохраняется как
  простой pip-интерфейс для пользователей и проверяется на соответствие lock.
- Внутренние события: `dataclass`, `TypedDict`, `Literal`, `Protocol`.
  Pydantic рассматривается только для недоверенных внешних DTO отдельным решением,
  чтобы без необходимости не утяжелять публичный self-hosted продукт.

## Этап 0. Baseline

- [x] Проверен статус git перед началом: рабочее дерево чистое.
- [x] Посчитан объём: 8 414 строк Python, 341 функция.
- [x] Зафиксированы крупные файлы:
  - `handlers/messages.py` — 910 строк;
  - `manage.py` — 848 строк;
  - `chat.py` — 692 строки;
  - `providers/claude_code.py` — 599 строк;
  - `providers/gemini.py` — 386 строк.
- [x] Зафиксировано отсутствие тестов, `pyproject.toml`, lock-файла, Ruff и
  Pyright-конфигурации.
- [x] Найдено около 105 широких `except Exception`; это baseline, а не допустимая
  норма.
- [x] Проверено локальное окружение: системный Python 3.9.6, рабочего `.venv`,
  `uv` и Python 3.10–3.12 нет.
- [x] Проверить фактическую версию Python на production VM без изменения сервера:
  Ubuntu 24.04.4, system/venv Python 3.12.3.
- [x] Утвердить и проверить матрицу Python: только 3.12; cp310 исключён после
  фактической ошибки отсутствующего `onnxruntime` wheel.
- [x] Снять обезличенные fixtures событий Claude, Codex и Gemini.
- [x] Зафиксировать исторические варианты SQLite-схем для migration tests.

## Этап 1. Toolchain и воспроизводимое окружение

- [x] Создать `pyproject.toml` с метаданными проекта и dev dependency groups.
- [x] Настроить pytest, asyncio mode и coverage.
- [x] Настроить Ruff без массового переписывания существующего кода.
- [x] Настроить Pyright в режиме `basic` с начальным ratchet-scope:
  `core/project_config.py` и новые тесты.
- [x] Установить `uv` и управляемый Python 3.12.13.
- [x] Сгенерировать `uv.lock`.
- [x] Сохранить рабочий pip-путь через `requirements.txt`.
- [x] Добавить автоматический тест синхронности runtime dependencies в
  `requirements.txt`/`pyproject.toml` и версии `pyproject`/`APP_VERSION`.
- [x] Добавить команды качества в `CONTRIBUTING.md`.
- [x] Синхронизировать команды качества с runtime-документацией.

Целевой локальный гейт:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run python -m compileall bot.py core handlers providers utils webapp/api
bash scripts/check_runtime.sh
```

## Этап 2. Characterization-тесты

### Privacy policy

- [x] Нет конфига → `private`.
- [x] Пустой, повреждённый или не-mapping YAML → `private`.
- [x] Неизвестный mode → `private`.
- [x] `local` никогда не виден CRM.
- [x] `crm` без `sync.enabled` или CRM id не виден CRM.
- [x] Каждый `send_*` флаг действует независимо и только при полном CRM opt-in.
- [x] Строковые boolean принимаются только по явно разрешённому списку;
  числовой YAML scalar opt-in не включает.
- [x] Storage-флаги не включаются неявно.
- [x] Кэш инвалидируется после изменения файла.
- [x] Ошибка чтения и отсутствие PyYAML дают `private` без утечки содержимого в лог.
- [x] Конфиги соседних проектов не влияют друг на друга.

### Access и авторизация

- [x] Claim первого владельца и `/logout` на уровне Telegram handler.
- [x] Несколько владельцев через `ADMIN_IDS` на уровне access policy.
- [x] Роли и статусы пользователей.
- [x] Режимы `open`, `approve`, `admins`.
- [x] Повторный `upsert_seen` не сбрасывает роль, статус и решение владельца.
- [x] Блокировка действует во всех режимах.
- [x] Нельзя повысить собственную роль без администратора.

### Mini App HMAC

- [x] Валидный Telegram `initData`, включая `signature` в data-check-string.
- [x] Подмена подписанного поля, `hash` или bot token.
- [x] Просроченный запрос.
- [x] Unicode и пустые обязательные поля.
- [x] Отсутствующий/нечисловой `auth_date` и дата далеко в будущем отклоняются.
- [x] Повторяющиеся параметры отклоняются до HMAC-проверки.
- [x] Dev skip-auth требует одновременно `HEREASSISTANT_ENV=development` и
  `WEBAPP_DEV_SKIP_AUTH=1`; одного skip-флага в production недостаточно.

### SQLite

- [x] Инициализация пустой БД и повторный `db.init()`.
- [x] Историческая схема до access/multi-user мигрирует всей цепочкой.
- [x] Существующие users/accounts/conversations и уникальные ограничения сохраняются.
- [x] Legacy admin-роль снимается у пользователя, которого больше нет в owners.
- [x] Ошибка миграции не оставляет частично применённое состояние.
- [x] Все DB/access-тесты работают только с временной БД.

### Telegram formatting

- [x] HTML escaping, inline code, code blocks, ссылки, цитаты, заголовки и списки.
- [x] Unicode и emoji.
- [x] Обычный текст разбивается по переносам или hard-limit без потери данных.
- [x] HTML-aware splitting гарантированно не разрывает теги между чанками.
- [x] Незакрытый Markdown не ломает конвертацию и отправку.
- [x] Ошибка HTML-отправки приводит к plain fallback.
- [x] Rich payloads и feature flags зафиксированы unit-тестами; debug dump не
  содержит отправляемый текст.
- [x] Полный Rich Message → HTML fallback зафиксирован на уровне orchestration.

## Этап 3. Типизированные события и CLI-парсеры

- [x] Ввести общий provider-контракт: `ProviderResult`, `ProviderMeta`,
  `ProgressMeta`/`ProgressCallback`, `ToolStep` и `FileEdit`.
- [x] Ввести отдельные внешние API DTO и модели access/conversation rows.
- [x] Заменить неявные provider state-словари dataclass/TypedDict-моделями;
  внешний tuple временно сохранён через `ProviderResult.as_tuple()`.
- [x] Зафиксировать pure helpers Claude/Gemini: text/thinking extraction, result
  preview, tool descriptions, cwd encoding и загрузка memory.
- [x] Вынести Claude/Gemini pure stream parsers из lifecycle subprocess; эвристику
  Codex session-id вынести в чистую функцию.
- [x] Добавить обезличенные Claude/Gemini JSONL golden fixtures.
- [x] Проверить partial/full text, reasoning, tool calls, dedup, edits, usage,
  malformed/non-object JSON, неизвестные события, bounded stderr, timeout/kill/reap
  и cancellation активного Claude CLI.
- [x] Добавить кроссплатформенный fake CLI для subprocess-тестов.

Целевая структура:

```text
providers/
├── models.py
├── process.py
└── parsers/
    ├── claude.py
    ├── codex.py
    └── gemini.py
```

## Этап 4. Декомпозиция

- [x] Разделить `handlers/messages.py`: buffer, attachments, orchestration,
  progress и delivery.
- [x] Вынести типизированный process-local message runtime state и чистые функции
  preview/signature/sensitive-edit filtering.
- [x] Вынести typed progress state, чистый HTML-renderer и политику
  FloodWait/quiet/throttling вместе с отдельным Telegram delivery boundary.
- [x] Вынести чистую сборку final payload и Telegram final delivery boundary.
- [x] Вынести rich-final orchestration и гарантированный classic table fallback.
- [x] Вынести live-session lifecycle: typing, rich draft, progress callback и
  heartbeat cleanup.
- [x] Вынести debounce/buffer с агрегацией частей и заменой timer.
- [x] Сократить оставшийся provider orchestration до legacy-модуля менее 500 строк.
- [x] Разделить providers на typed models, pure parsers и subprocess runtime;
  общий process helper вынесен и используется Claude/Gemini runtime.
- [x] Разделить `chat.py`: session, commands, renderer, resume и identity.
  - [x] Typed terminal renderer и provider progress stream.
  - [x] Typed Session, identity helpers и native Claude resume store.
  - [x] Command router с явными DB/workspace/resume dependencies.
- [x] Разделить `manage.py`: terminal UI, env, accounts, access, audit и process
  services.
  - [x] Typed `.env` template/parser/state/admin IDs.
  - [x] Terminal UI primitives и raw-key boundary.
  - [x] CLI process и account auth-marker helpers.
  - [x] Account SQLite repository и audit read services.
  - [x] Interactive account/login actions и header coordinator.
- [x] Не допускать новых модулей крупнее 300–400 строк без обоснования.
- [x] Каждый перенос проверен зелёным checkpoint; commit/PR намеренно не создавались
  без отдельной пользовательской команды.

## Этап 5. Исключения и диагностика

- [x] Классифицировать все широкие исключения.
- [x] Заменить ожидаемые ошибки конкретными типами в critical scope.
- [x] Не проглатывать ошибки privacy, auth, DB и startup-инвариантов.
- [x] Для допустимых boundary-catch зафиксировать причины в exception audit.
- [x] Запретить новые необоснованные `except Exception` через ratchet.
- [x] Проверить, что логи не содержат промпты, токены и содержимое project config.

## Этап 6. CI и релизный гейт

- [x] Python 3.12 matrix: Ubuntu и Windows CI.
- [x] `uv sync --frozen`, Ruff, Pyright, pytest и compileall — финальный прогон.
- [x] Сборка Nuxt Mini App (`npm ci && npm run generate`).
- [x] Secret scan и проверка runtime-файлов через repository hygiene gate.
- [x] Smoke-тест обновления существующей SQLite БД.
- [x] Ubuntu production baseline проверен read-only; Windows legacy закреплён
  platform unit-тестами и CI job.
- [x] Обновить README/CONTRIBUTING и release notes.

## Порядок поставки

1. Toolchain и lock.
2. Privacy/access tests.
3. HMAC/SQLite tests.
4. Provider fixtures, typed events и pure parsers.
5. Formatting/Rich Message tests.
6. Декомпозиция messages/providers.
7. Декомпозиция chat/manage.
8. Exception audit, CI и release gate.

## Условия перехода к grammY/TypeScript spike

- Критические characterization-тесты зелёные.
- SQLite и provider-event контракты описаны моделями и fixtures.
- Privacy/auth отрицательные сценарии покрыты.
- Есть измеримый baseline запуска, памяти и latency.
- TypeScript spike сравнивается с Python на одинаковых fixtures и не меняет
  production до отдельного решения.

## Журнал прогресса

### 2026-07-11

- Создан рабочий hardening-план.
- Снят первичный baseline репозитория и локального Python-окружения.
- Обнаружено расхождение окружения: системный Python 3.9.6 против заявленного в
  историческом ТЗ Python 3.12; локальный `.venv` отсутствует.
- Установлены `uv` 0.11.28 и управляемый CPython 3.12.13; создан `uv.lock` на 62
  пакета и локальное окружение из lock.
- Добавлены `pyproject.toml`, Ruff, Pyright, pytest и первый набор из 31 privacy
  characterization-сценария.
- Первый полный Pyright baseline выявил 73 legacy-ошибки. Чтобы не маскировать их
  глобальными ignore, обязательный scope начат с покрытого `project_config` и будет
  расширяться по модулям.
- Privacy-набор зелёный: 31 тест, 94% branch-aware coverage модуля.
- Добавлены access characterization-тесты с отдельной временной SQLite: матрица
  режимов, owner/admin/user/denied, переходы ролей, поиск и сохранение решений.
- Текущий итог: 62 теста, 95% совокупного coverage для `project_config`, `access`
  и `db`; Ruff, format, scoped Pyright и compileall зелёные.
- Contributor workflow обновлён: uv — воспроизводимый основной путь, pip/venv
  сохранён как простой OSS fallback.
- Добавлены historical SQLite fixtures: fresh/legacy init и повторная миграция
  сохраняют данные и проходят идемпотентно.
- HMAC-тесты выявили и закрыли security-gap: подписанный `initData` раньше принимался
  без валидного `auth_date` и с датой из будущего. Теперь timestamp обязателен,
  действует max-age и 60-секундный допуск clock skew.
- Текущий итог: 83 теста, 96% совокупного branch-aware coverage критического
  scope; Ruff, format и scoped Pyright зелёные.
- Production baseline проверен read-only: Ubuntu 24.04.4, system/venv Python 3.12.3.
- Dev skip-auth усилен двумя обязательными флагами; production-дефолт всегда с
  авторизацией. PM2 development profile и `.env.example` синхронизированы.
- Provider helper contract покрыт 39 тестами без изменения subprocess lifecycle:
  Claude/Gemini text, thinking, tool descriptions, result preview и memory loading.
- Telegram formatting/rich/common покрыты: escaping, Markdown, code/pre, splitting,
  payloads, safe debug metadata и fallback.
- Автотест блокирует дрейф runtime-зависимостей между `requirements.txt` и
  `pyproject.toml`, а также версии между `pyproject` и `APP_VERSION`.
- Текущий итог: 165 тестов; `uv lock --check`, расширенный scoped Pyright, Ruff,
  format, compileall и `git diff --check` зелёные; secret scan чистый.
- Следующий шаг на тот момент: typed provider events и pure parser extraction;
  выполнено в следующей записи журнала.

### 2026-07-11 — typed providers и parser extraction

- Добавлены `ProviderResult`, typed meta/progress contracts, `ToolStep` и
  `FileEdit`; старые consumers продолжают получать совместимый tuple.
- Claude stream parser вынесен из subprocess runtime и проверяется обезличенным
  JSONL fixture: thinking/text, tools, dedup, edits, steps, usage, session,
  error reason и rate-limit partial result.
- Claude runtime покрыт fake-subprocess parity-тестом; 32 MiB stream limit,
  stdin, progress и bounded error-path сохранены.
- `bypassPermissions` теперь блокируется allowlist и заменяется на `acceptEdits`.
- Gemini stream parser и runtime вынесены аналогично: delta/full messages,
  user echo, edits/tools, usage, progress и stderr проверены.
- Codex session-id heuristic вынесена в чистую тестируемую функцию; все три
  provider возвращают результат через единый typed contract.
- Claude runtime уменьшен с 599 до 349 строк, Gemini — с 386 до 307; JSON state
  parsing полностью отсутствует в runtime-файлах.
- Общий subprocess lifecycle (`resolve_cli_argv`, stdin, timeout/kill/reap) вынесен
  в `providers/process.py` и покрыт Windows `.cmd`/`.bat`/PowerShell fixtures.
- После устранения lifecycle-дублей Claude runtime уменьшен до 304 строк,
  Gemini — до 254.
- После перемещения репозитория локальный `.venv` пересинхронизирован из lock;
  основной монорепо обновлён на новый путь
  `~/Visual Studio Code/Creative Agency Here/HereAssistant`.
- Текущий итог: 198 тестов; полный provider Ruff scope, format, Pyright,
  compileall, `uv lock --check` и `git diff --check` зелёные.
- Следующий шаг: timeout/cancellation tests общего process lifecycle, затем
  декомпозиция `handlers/messages.py` под уже типизированный provider contract.
- Timeout/cancellation failure paths закрыты: malformed JSON пропускается,
  cancellation убивает Claude CLI, общий helper делает kill+reap после timeout.
- Начата декомпозиция `handlers/messages.py`: глобальные словари/counter заменены
  `MessageRuntimeState`, форматирование и privacy-фильтр путей вынесены в
  `message_formatting.py`.
- Voice/attachment preparation вынесена в `message_attachments.py`: успешная и
  упавшая транскрибация, bounded status preview, сохранение исходного файла при
  ошибке и multi-attachment prompt покрыты тестами.
- `handlers/messages.py` уменьшен с текущего форматированного baseline 929 до
  776 строк.
- Progress state и чистый HTML-renderer вынесены в `message_progress.py`:
  escaping заголовка, attachments, structured/flat steps, thinking, overflow и
  quiet marker покрыты characterization-тестами.
- Политика FloodWait/quiet/throttling отделена от Telegram I/O и покрыта тестами:
  cooldown не обходится даже forced update, backoff ограничен максимумом, серия
  успешных edit постепенно возвращает базовый интервал.
- Telegram progress delivery вынесен из orchestration в отдельный boundary:
  создание/edit сообщения, duplicate suppression, `TelegramRetryAfter`, expected
  `not modified` и legacy flood-errors покрыты асинхронными тестами.
- Исправлен обнаруженный дефект orchestration: `force=True` на смене tool-state
  раньше не обходил обычный локальный интервал и потому не форсировал отрисовку.
- `handlers/messages.py` после delivery extraction уменьшен до 715 строк.
- Текущий итог: 236 тестов; полный scoped Ruff/format, Pyright, compileall,
  `uv lock --check`, `git diff --check` и secret scan зелёные.
- Финальная сборка ответа вынесена в чистый `message_final.py`: preview длинного
  ответа, inline/file steps, HTML escaping и UTF-8 BOM attachments закреплены
  тестами.
- Telegram final delivery вынесен в отдельный boundary: progress edit, fallback
  через `send_long`, один retry после FloodWait, отдельная edits-кнопка и отправка
  документов/таблиц проверяются без запуска провайдера.
- `handlers/messages.py` после final extraction уменьшен до 615 строк.
- Текущий итог перед полным прогоном: 245 тестов; targeted Ruff/format, Pyright и
  compileall зелёные.
- Debounce buffer вынесен в `message_buffer.py`: склейка текста/вложений, замена
  активного timer и вызов flush по thread key покрыты асинхронными тестами.
- `handlers/messages.py` после buffer extraction уменьшен до 601 строки.
- Текущий итог: 247 тестов; полный scoped Ruff/format, Pyright, compileall,
  `uv lock --check`, `git diff --check` и secret scan зелёные.
- Следующий шаг: полный release-gate, затем вынести rich-final orchestration либо
  перейти к `chat.py` после оценки оставшихся связей в message handler.
- Rich-final orchestration вынесен в `message_rich_final.py`: Markdown payload,
  ограничение step-chain, rich send, progress cleanup и classic table fallback
  покрыты тестами.
- Исправлен fallback-gap: после ответа Bot API `method not found` глобальный rich
  feature flag выключался до fallback-проверки, поэтому таблицы могли остаться
  сырым Markdown. Теперь решение о fallback использует snapshot до rich-вызова.
- `handlers/messages.py` уменьшен до 584 строк.
- Текущий итог: 254 теста; полный scoped Ruff/format, Pyright, compileall,
  `uv lock --check`, `git diff --check` и secret scan зелёные.
- Следующий шаг: полный release-gate, затем аудит оставшегося orchestration перед
  переходом к `chat.py`.
- Live-session lifecycle вынесен в `message_live.py`: typing и progress
  heartbeat, initial progress, rich-draft throttling/fallback и provider progress
  callback теперь имеют единый typed owner и идемпотентный cleanup.
- `handlers/messages.py` уменьшен с 584 до 469 строк и вышел ниже целевого
  предела 500 строк для legacy orchestration-модуля.
- Текущий итог: 259 тестов; полный scoped Ruff/format, Pyright, compileall,
  `uv lock --check`, `git diff --check` и secret scan зелёные.
- Следующий шаг: полный release-gate, затем начать декомпозицию `chat.py`.
- Начата декомпозиция `chat.py`: ANSI palette, потоковый Markdown, typed progress
  state, tool/thinking/text renderer и run summary вынесены в `chat_renderer.py`.
- Исправлен renderer-gap: поздняя reasoning-дельта больше не вклинивается внутрь
  уже начатого answer stream и не повреждает отображаемый текст.
- `chat.py` уменьшен с 692 до 496 строк; renderer покрыт 7 characterization-тестами.
- Текущий итог перед полным прогоном: 266 тестов; targeted Ruff/format, Pyright и
  compileall зелёные.
- Следующий шаг: полный release-gate, затем вынести native resume store и identity.
- `Session`, user identity lookup/display и native Claude resume store вынесены в
  `chat_sessions.py`/`chat_identity.py`; resume entries заменены типизированным
  `ResumableSession`.
- Resume parser покрыт malformed/meta/string/block content, сортировкой, лимитами
  и non-Claude boundary; широкие file/JSON catches заменены `OSError` и
  `JSONDecodeError`.
- `chat.py` уменьшен до 419 строк.
- Текущий итог: 273 теста; полный scoped Ruff/format, Pyright, compileall,
  `uv lock --check`, `git diff --check` и secret scan зелёные.
- Следующий шаг: полный release-gate, затем вынести command router.
- Slash-command router вынесен в `chat_commands.py`; account/user/workspace/resume,
  input/output и clear-process передаются явными dependencies и тестируются без
  SQLite либо интерактивного терминала.
- Characterization покрывает exit aliases, model/account/user/cwd session reset,
  typed resume selection, bounded diff, clear и unknown command.
- `chat.py` уменьшен с исходных 692 до 281 строки; этап декомпозиции завершён.
- Текущий итог: 282 теста; полный scoped Ruff/format, Pyright, compileall,
  `uv lock --check`, `git diff --check` и secret scan зелёные.
- Следующий шаг: полный release-gate, затем начать декомпозицию `manage.py`.
- Начата декомпозиция `manage.py`: `.env` template/parser/state/admin IDs вынесены
  в `manage_env.py` с default-deny поведением для missing/placeholder значений.
- Из шаблона удалена подсказка `bypassPermissions`; допустимыми документированы
  только `acceptEdits` и `default`.
- `manage.py` включён в Ruff lint-ratchet; старые semicolon/one-line-if и
  ambiguous-name нарушения устранены без изменения поведения.
- Текущий итог: 289 тестов; полный scoped Ruff/format, Pyright, compileall,
  `uv lock --check`, `git diff --check` и secret scan зелёные.
- Следующий шаг: полный release-gate, затем вынести account auth/process helpers.
- CLI process/auth-marker helpers вынесены в `manage_process.py`: Windows npm
  shim argv, environment merge и Claude/Codex/Gemini login markers покрыты тестами.
- ANSI/box/menu/raw-key primitives вынесены в `manage_ui.py`; fallback ввода и
  расчёт ширины без ANSI зафиксированы тестами.
- `manage.py` уменьшен до 710 строк; текущий checkpoint: 299 тестов, полный
  scoped Ruff/format, Pyright, compileall, lock/diff и secret scan зелёные.
- Audit queries вынесены в `manage_audit.py`: usage/rate-limit, Telegram events,
  SSH history и formatters используют конкретные SQLite/JSON/process exceptions.
- Account CRUD и DTO вынесены в `manage_accounts.py`; duplicate label стал
  доменной ошибкой `AccountExistsError`, label sanitization покрыт тестами.
- Текущий checkpoint: 313 тестов; полный scoped Ruff/format, Pyright, compileall,
  lock/diff и secret scan зелёные; `manage.py` — 665 строк.
- Header/getMe dashboard вынесен в `manage_header.py`, read-only views — в
  `manage_views.py`, paths/provider DTO — в `manage_config.py`.
- Interactive account/login/runtime actions вынесены в `manage_actions.py`;
  `manage.py` теперь entrypoint/coordinator на 232 строки, actions — 271 строка.
- Декомпозиция `manage.py` завершена; переработанный production scope не содержит
  модулей свыше 400 строк (исключая characterization tests).
- Текущий итог перед полным прогоном: 320 тестов.

### 2026-07-11 — final hardening gate

- Access handler coverage закрывает first-owner claim, invalid claim, DB-user
  logout, last-owner claim rotation/restart и запрет self-role mutation.
- SQLite migrations переведены на явную транзакцию; injected DDL failure
  подтверждает rollback ранее добавленной колонки.
- Telegram HTML splitter закрывает/reopens nested tags и не разрывает entities;
  rich → classic fallback проверен orchestration tests.
- Добавлены Codex fixture/pure parser, cross-platform real fake CLI и typed
  internal/Web API DTO с validation внешних task payloads.
- Global Ruff lint/format охватывает весь Python tree. Exception audit фиксирует
  84 классифицированных legacy boundary catches и zero-broad critical scope.
- CI добавляет Ubuntu/Windows Python 3.12 и Nuxt generate. Python 3.10 проверен
  фактически и исключён: locked `onnxruntime` не имеет cp310 wheel.
- `scripts/quality_gate.sh`: 337 тестов, Ruff, format, Pyright, compileall,
  `uv lock --check`, exception ratchet и repository hygiene — зелёные.
- `npm ci && npm run generate` и повторный `npm run generate` успешны;
  `.output/public` создан, generated `dist` symlink удалён и запрещён hygiene gate.
- `scripts/check_runtime.sh` зелёный: три CLI найдены, зависимости импортируются,
  production auth flags безопасны, SQLite init и frontend artifact исправны.
- Добавлены release notes и измеримый Python startup/RSS baseline для будущего
  grammY/TypeScript spike. Деплой, commit и push не выполнялись.

### 2026-07-12 — production rollout and isolation close-out

- Hardening 0.4.0 опубликован в обоих remote и развёрнут на production после
  SQLite backup; миграции, integrity check, PM2 runtime и API health прошли.
- Provider account, conversations, projects, cwd authorization и provider memory
  изолированы по пользователю; произвольный enabled-account fallback удалён.
- WebApp опубликован по `/webapp/` через ограниченный SSH local-forward, сохранив
  существующий landing; API, вложенные Nuxt assets и Telegram menu проверены.
- RTK v0.43.0 установлен с pinned checksum, telemetry disabled и отдельной
  статистикой по account home; `/rtk` и `/api/rtk` показывают фактическую экономию,
  а сырые аргументы команд после запуска очищаются.
- Добавлен user-scoped Git project flow: clone/status/pull/worktree и подтверждаемый
  push в разрешённые hosts; рабочие каталоги и worktrees регистрируются на владельца.
- Закрыты последние WebApp-утечки: active task, recent actions и file changes
  фильтруются по Telegram user; глобальный bot.log доступен только primary admin.
- Итоговый quality gate: 360 тестов, Ruff, format, Pyright, compileall,
  `uv lock --check`, exception ratchet и repository hygiene — зелёные.
- Остающийся архитектурный предел: CLI разных людей пока запускаются под одним
  Unix UID. До приватных клиентских репозиториев нужны отдельные OS runner users.

### 2026-07-12 — OS runner foundation

- Добавлен выключенный по умолчанию fail-closed boundary для provider subprocess:
  Telegram user, private account owner, Unix runner, CLI home, provider executable
  и resolved project root сверяются до запуска.
- Claude, Codex, Gemini и общий CLI runtime переведены на единый spawn boundary;
  текущий production-контур без `OS_RUNNERS_ENABLED=1` сохраняет прежнее поведение.
- Root-installed wrapper получает минимальное окружение без Telegram/WebApp/API
  secrets, очищает RTK command history внутри runner и экспортирует только агрегаты.
- Добавлены installer skeleton, root-owned config format, sudoers/runbook и rollback
  в `docs/os-runners.md`; 382 теста и полный локальный quality gate зелёные.
- Production activation остаётся заблокирован до user-scoped Git broker и
  безопасного attachment staging: включать runners раньше нельзя.
- Git broker завершён: runner принимает только allowlisted status/remote/pull,
  push origin|github, clone с разрешённого host и worktree внутри project root.
- Telegram attachments складываются в `downloads/<user_id>` с mode `0640`;
  production runbook требует отдельную Unix-группу на пользователя.
- Итог checkpoint: 388 тестов и полный quality gate зелёные; включение production
  требует реальных Unix users, memberships, credentials и canary.
- Первый production runner активирован для Ильи: real Claude profile, project
  workspace, attachment staging, Git broker и RTK aggregates работают под
  отдельным Unix UID; bot/API и SQLite прошли smoke/integrity checks.
- Паша остаётся неактивированным до получения Telegram ID и собственных CLI
  credentials. Private Gitea push из runner также ждёт отдельный user token.

### 2026-07-12 — следующий этап: Git identity и mobile workspace

- Проектирование user-owned Git authorization и Claude-inspired mobile WebApp
  зафиксировано в `docs/git-auth-and-mobile-webapp.ru.md`.
- Git token нельзя помещать в HOME code runner: coding agent способен выполнять
  shell-команды. Production target — отдельный per-user Git broker UID и vault,
  который не возвращает raw credential агенту.
- Основной UX подключения — WebApp; Telegram остаётся identity/notification
  channel, CLI — headless/admin fallback. Gitea идёт через OAuth2 + PKCE, GitHub —
  через GitHub App с выбранными repositories; ручной PAT только как one-time fallback.
- P0 завершён: multi-remote dry-run preflight, stable Git error codes, metadata-only
  `git_connections` / `git_repository_grants` / ephemeral OAuth session schema,
  typed WebApp DTO и owner-isolation/migration tests добавлены. Public DTO не
  сериализует `vault_ref`; revoke обнуляет ссылку и выключает repository grants.
- Полный quality gate: 403 теста, Pyright, Ruff/format, compileall, lock,
  exception ratchet и repository hygiene — зелёные.
- Следующая пачка P1 — отдельный Git broker UID/config и vault interface.
  Production credentials и текущие runner config не менялись.

### 2026-07-13 — P1 Git broker isolation foundation

- Provider и authenticated Git разведены по двум обязательным mapping:
  `OS_RUNNER_MAP` и `OS_GIT_RUNNER_MAP`; одинаковый Unix UID запрещён, отсутствующий
  Git mapping fail-closed без fallback на coding runner.
- Root wrapper различает provider и `git_broker` configs: provider config не
  принимает Git mode, Git config не содержит accounts и не запускает CLI agents.
- Добавлен root-installed `hereassistant-git-credential` proxy стандартного Git
  credential protocol. Он принимает только HTTPS `get`, отправляет vault service
  только host/repository path и блокирует traversal/unsafe socket permissions.
- Git environment сбрасывает inherited credential helpers и terminal/askpass
  prompts; helper/socket задаются только root-owned runner config.
- Добавлен Linux vault service и systemd unit: `SO_PEERCRED` проверяет Git UID,
  SQLite grant — owner/host/repository/read-write permission, а credential bundle
  загружается через `LoadCredentialEncrypted` только в память.
- Root-owned vault admin принимает OAuth/PAT credential только через bounded stdin,
  сверяет connection с `user_id` из root config, обновляет encrypted bundle через
  `systemd-creds` и `fsync + os.replace`, затем перезапускает только активный
  per-user vault service. Путь к SQLite теперь также закреплён root config и не
  приходит через argv.
- При ошибке encrypt прежний ciphertext остаётся неизменным; revoke удаляет только
  opaque ref выбранного connection. Токен не попадает в argv/env/stdout/logs или
  plaintext-файл. OAuth callback и production canary остаются следующим подэтапом
  P1; текущий production не изменён.
- Repository-controlled Git execution закрыт дополнительным gate: local config
  keys allowlisted, hooks/system/global config и unsafe protocols отключены; при
  включённом helper control files обязаны иметь Linux immutable flag, что закрывает
  замену после аудита. До реального credential всё ещё нужен negative canary.
- Полный quality gate: 442 теста, Pyright, Ruff/format, compileall, lock,
  exception ratchet и repository hygiene — зелёные; installer проходит `bash -n`.
- Gitea public-client OAuth2 + PKCE подключён к WebApp API: exact-host app config,
  HMAC-only single-use state, S256 verifier без хранения plaintext, bounded HTTPS
  exchange без redirects, owner-scoped list/revoke и прямой stdin transfer в Git
  vault. Replay/cross-user/token-in-DB negative tests добавлены; production не
  менялся. Автоматический refresh-token flow остаётся отдельным P2 hardening.
- В WebApp активирован раздел `Настройки → Git`: список owner-scoped connections,
  переход на Gitea consent, callback result, reconnect/revoke и список настроенных
  exact hosts. OAuth callback синхронизирует metadata доступных Gitea repositories
  как disabled-by-default grants; пользователь явно включает каждый repository в
  picker, исчезнувшие repositories отключаются. Nuxt production build проходит.
- OAuth expiry закрыт fail-closed: WebApp переводит просроченный connection в
  `expired`, а vault SQL независимо от UI не выдаёт credential после `expires_at`.
  До безопасного refresh-token broker flow пользователь переподключает account.
