# План заимствований из jcode в HereAssistant

**Статус:** в работе. Пункты 1 и 3 корзины 1 выполнены 2026-07-29.

Дата фиксации: 2026-07-29. Источник — разбор открытого харнесса
[jcode](https://github.com/1jehuang/jcode) (Rust, MIT): девять тематических отчётов по его
подсистемам плюс фактическая карта HereAssistant. Отчёты (`reports/01-memory.md` —
`reports/09-runtime.md`, `reports/codex-recon.md`) — рабочие материалы разбора, они остались
вне репозитория; ссылки на них в тексте указывают, откуда взят факт. План не предполагает
изменений в самом jcode и не переносит его код — только инварианты и приёмы.

## Итоговое решение

Сейчас берём пять заимствований: гарантированную отмену дочернего CLI, безопасный разбор
native transcript, настоящую очередь следующего turn-а, каталог resume для Claude/Codex и
детектор опасных shell-команд в режиме монитора. Это даёт заметную пользу без новой зависимости,
без миграции SQLite и без изменения privacy-default.

Семантическую память, auto-poke, блокирующие approvals, headless auth, внешний live API,
рой и overnight переносим только после решений владельца и включаем только явным opt-in.
Контекстную компакцию, отдельный session journal, ACP, deep-swarm и прямой OAuth под видом
официальных CLI не переносим.

## Правила оценки и неизменяемые ограничения

- Польза `П`: 1 — почти нет продуктового эффекта, 5 — закрывает существенный риск/сценарий.
- Риск `Р`: 1 — локальная совместимая правка, 5 — privacy/ToS/account-loss/автономный side effect.
- Индекс приоритета: `100 × П / (середина часов × (1 + 0,5 × (Р − 1)))`. Выше — лучше.
- Условие владельца сильнее индекса: высокий балл не разрешает сетевую загрузку, новый секретный
  контур или автономный расход подписки.
- Default deny остаётся в `core/project_config.py:39`, `core/project_config.py:57`,
  `core/project_config.py:134`; никакая новая функция не включается из-за отсутствующего/битого YAML.
- Python остаётся `>=3.12,<3.13` (`pyproject.toml:6`), обязательны Ubuntu и Windows 3.12
  (`.github/workflows/python-hardening.yml:13`), `uv sync --frozen` и `uv lock --check`
  (`scripts/quality_gate.sh:5`, `scripts/quality_gate.sh:12`).
- В первой корзине зависимости не добавляются. Позднее изменение зависимостей обязано одновременно
  обновить `pyproject.toml`, `requirements.txt`, `uv.lock` и пройти frozen gate на обеих ОС.
- Миграции только в существующем стиле: идемпотентный `SCHEMA`, проверка через
  `PRAGMA table_info`, транзакция `core/db.py:328`; версионированных файлов миграций не вводим.
- Prompt, команды, tool input/output, векторы и confidence не считаются безусловно безопасными
  метаданными. Без отдельного project opt-in они не пишутся в SQLite, логи, outbox или WebSocket.
- После каждого завершённого шага выполняется `scripts/quality_gate.sh:1`; промежуточный контракт
  должен оставаться обратно совместимым.

## Сводный рейтинг

Внутри каждой корзины строки отсортированы по индексу; решение о корзине и обязательное условие
имеют приоритет над числом (поэтому условный или запрещённый пункт не поднимается в «сейчас»).

| № | Заимствование | Корзина | П | Часы | Р | Индекс |
|---:|---|---|---:|---:|---:|---:|
| 1 | Гарантированный cancel/kill/reap для нестримингового CLI | сейчас | 5 | 3–4 | 1 | 142,9 |
| 2 | Fail-soft и privacy-safe разбор native transcript | сейчас | 4 | 4–5 | 1 | 88,9 |
| 3 | Очередь следующего turn-а вместо параллельного запуска | сейчас | 4 | 6–8 | 2 | 38,1 |
| 4 | Resume-каталог Claude и Codex | сейчас | 4 | 6–8 | 2 | 38,1 |
| 5 | Опасные shell-команды: POSIX-классификатор + монитор | сейчас | 5 | 16–20 | 2 | 18,5 |
| 6 | Блокирующий pre-tool approval через hooks | позже, условно | 5 | 10–14 | 4 | 16,7 |
| 7 | Headless/scriptable login через официальные CLI | позже, условно | 4 | 10–14 | 3 | 16,7 |
| 8 | Импорт pi/OpenCode/Cursor как read-only каталог | позже, условно | 2 | 6–10 | 2 | 16,7 |
| 9 | Плоская семантическая память на локальном ONNX | позже, условно | 5 | 14–20 | 3 | 14,7 |
| 10 | Auto-poke + max-turn + confidence stepping | позже, условно | 4 | 10–18 | 4 | 11,4 |
| 11 | Аутентифицированный live harness API | позже, условно | 4 | 16–20 | 3 | 11,1 |
| 12 | jcode как пятый CLI-провайдер | позже, только с разрешением | 3 | 10–14 | 5 | 8,3 |
| 13 | LLM-extraction/dedup/decay памяти | позже, условно | 3 | 12–20 | 4 | 7,5 |
| 14 | Реестр роя, task guard и reclaim | позже, условно | 3 | 25–35 | 4 | 4,0 |
| 15 | Overnight/ambient scheduler | позже, условно | 2 | 20–28 | 5 | 2,8 |
| 16 | PNG/HTML productivity и overnight review | не берём | 1 | 8–12 | 2 | 6,7 |
| 17 | Прямой OAuth/API с identity/UA официального CLI | не берём | 2 | 16–24 | 5 | 5,0 |
| 18 | Snapshot + JSONL-журнал полного transcript | не берём | 1 | 10–14 | 3 | 4,2 |
| 19 | Граф памяти, BFS и кластеризация | не берём | 2 | 15–21 | 4 | 4,4 |
| 20 | Reactive compaction поверх истории HereAssistant | не берём | 1 | 13–17 | 3 | 3,3 |
| 21 | ACP/Unix-socket bridge | не берём | 1 | 12–18 | 3 | 3,3 |
| 22 | Deep-swarm DAG/expand-node | не берём | 1 | 25–35 | 5 | 1,1 |
| 23 | Rust-механика tract/tokio/flock/ACL как есть | не берём | 0 | 16–24 | 3 | 0 |

## Корзина 1 — берём сейчас

### 1. Гарантированный cancel/kill/reap нестримингового CLI

Источник идеи: инвариант отмены и process-global reachability из `reports/09-runtime.md`.

- Результат: отмена task или timeout всегда убивает и `await`-ит дочерний Codex; повторная отмена
  идемпотентна. Сейчас `_exec` обрабатывает timeout, но не `CancelledError`
  (`providers/base.py:109`, `providers/base.py:119`), тогда как stream-провайдеры уже убивают
  subprocess (`providers/claude_code.py:269`).
- Файлы: `providers/process.py:52`, `providers/base.py:87`,
  `tests/providers/test_process.py:1`, `tests/providers/test_codex.py:1`.
- Новый модуль: нет; новый helper `cancel_and_reap()` в `providers/process.py`.
- SQLite: миграция не нужна.
- Совместимость: сигнатуры `CLIProvider.run/_exec` не меняются; меняется только cleanup при
  cancel/timeout. Успешные и ошибочные ответы не ломаются.
- Проверка: fake process для cancel-before-exit, already-exited и kill-race; затем полный gate.

### 2. Fail-soft и privacy-safe разбор native transcript

Источник: единый extractor, `[image]`, Unknown-fallback и broken-line tolerance из
`reports/04-import.md`; текущая граница — `core/native_sessions.py:68` и
`core/native_sessions.py:124`.

- Результат: transcript читается построчно, битые/новые записи не роняют turn; рекурсивный
  extractor по умолчанию пропускает tool input/output/reasoning, заменяет image/base64 на `[image]`.
  Существующий порядок «сначала policy, потом open» (`core/native_sessions.py:194`,
  `core/native_sessions.py:201`) сохраняется.
- Файлы: `core/native_sessions.py:84`, `core/native_sessions.py:124`,
  `tests/core/test_native_sessions.py:1`.
- Новый модуль: нет.
- SQLite: миграция не нужна.
- Совместимость: `NativeSessionResult` и `ingest_hook()` не меняются; лимиты 8 МиБ и 20 000
  символов остаются. Existing plain text продолжает давать тот же turn.
- Проверка: fixtures с broken JSON, unknown block, base64 image, tool_result, oversized file,
  symlink/outside-home и оба выключенных sync-флага; затем полный gate.

### 3. Очередь следующего turn-а вместо параллельного запуска

Источник: soft-interrupt queue и safe-point injection из `reports/09-runtime.md`, адаптированные
к шлюзу, который не владеет внутренним tool loop CLI.

- Результат: при `INTERRUPT_ON_NEW_MESSAGE=0` новый ввод не запускает второй CLI параллельно.
  Он агрегируется process-local и стартует после `finally` текущего turn-а с актуальным
  `provider_session_id`. При `=1` сохраняется нынешняя отмена. Сейчас ветка пишет «поставил в
  очередь», но всё равно создаёт task (`handlers/messages.py:164`, `handlers/messages.py:181`).
- Файлы: `handlers/messages.py:127`, `handlers/messages.py:164`,
  `handlers/message_state.py:23`, `handlers/message_buffer.py:16`;
  новый `handlers/message_queue.py`; тесты `tests/handlers/test_message_state.py:1`,
  `tests/handlers/test_message_buffer.py:1`, новый `tests/handlers/test_message_queue.py`.
- Новый модуль: `handlers/message_queue.py` — чистые операции enqueue/pop/merge.
- SQLite: миграция не нужна; prompt/attachments не персистируются.
- Совместимость: default `INTERRUPT_ON_NEW_MESSAGE=1` не меняется; в queue-mode вместо
  неконтролируемой конкуренции будет один активный turn на `ThreadKey`.
- Проверка: два/три входа во время active task, coalescing, cancel-mode, exception текущего turn,
  отсутствие двойного `prov.run`; затем полный gate.

### 4. Resume-каталог Claude и Codex

Источник: bounded top-N, Codex JSONL и фильтр служебных заголовков из `reports/04-import.md`.
Текущий reader знает только Claude (`chat_sessions.py:49`, `chat_sessions.py:57`).

- Результат: `/resume` перечисляет не более 20 свежих сессий выбранного Claude/Codex account.
  Codex читается из `<CODEX_HOME>/sessions/YYYY/MM/DD/*.jsonl`; session id/cwd берутся только
  из `session_meta`, title — из первого содержательного user message. Tool/reasoning/base64
  не попадают в title или БД.
- Файлы: новый `core/session_import.py`; `chat_sessions.py:17`, `chat_sessions.py:49`,
  `tests/test_chat_sessions.py:1`; новые fixtures только под `tests/fixtures/sessions/`.
- Новый модуль: `core/session_import.py` с typed records, bounded catalog и safe title extractor.
- SQLite: миграция не нужна; каталог read-only, чужие файлы не копируются.
- Совместимость: `ResumableSession` и Claude resume сохраняются; Codex добавляется по provider
  dispatch. Путь обязан разрешаться внутри `cli_home_path`.
- Проверка: Claude legacy, Codex dated path, broken line, service prompt, symlink/outside-home,
  file-size cap, top-20; затем полный gate.

### 5. Опасные shell-команды: POSIX-классификатор + эфемерный монитор

Источник: таблица 20 правил `reports/06-safety.md`; unified event idea —
`reports/05-harness-api.md`. HereAssistant сам команды не исполняет
(`providers/base.py:41`), а raw tool input сейчас теряется после parser
(`providers/parsers/claude.py:71`, `providers/parsers/claude.py:273`).

- Результат: stdlib-классификатор `Safe/Low/Confirm/Catastrophic` реализует все 20 правил.
  Claude/Qwen/Gemini передают raw shell call только в transient `ProgressMeta`; callback немедленно
  классифицирует, удаляет raw command перед записью в `ProgressState` и один раз за turn
  уведомляет пользователя о Confirm/Catastrophic без текста команды. `ProviderMeta`, events,
  SQLite, logs и WebSocket raw command не получают.
- Файлы: новый `core/command_risk.py`; `providers/models.py:26`;
  `providers/parsers/claude.py:54`, `providers/parsers/gemini.py:39`;
  `providers/claude_code.py:226`, `providers/qwen_code.py:142`, `providers/gemini.py:225`;
  `handlers/message_live.py:117`; соответствующие core/provider/handler tests.
- Новые модули: только `core/command_risk.py`.
- SQLite: миграция не нужна; журнал рисков отсутствует.
- Совместимость: `ProgressMeta` расширяется optional-полем; Safe/Low молчат. Codex явно помечен
  как «монитор недоступен», потому что `providers/codex.py:29` игнорирует progress.
- Ограничение: bash/POSIX parser одинаково запускается на Windows, но PowerShell/cmd не
  классифицируется как bash; неоднозначность даёт `Confirm/unsupported`, не ложный `Safe`.
- Проверка: все 20 правил, wrappers, `sh -c`, chain/pipe/redirection, `$HOME`, temp paths,
  Windows unsupported, dedup alerts, отсутствие raw command в state/final meta; затем полный gate.

## Корзина 2 — берём позже / при условии

### 6. Блокирующий pre-tool approval — только после доказанного hook-контракта

- Условие: pinned CLI обязан документированно остановить tool до ответа hook на Ubuntu/Windows;
  для каждого provider нужен passing integration fixture. Без этого остаётся монитор.
- Реализация: новый `core/permission_requests.py`, hook adapter в `core/native_hooks.py:41`,
  Telegram decision handler и `permission_requests` в `core/db.py:227`; raw command не хранить,
  только hash, класс риска, status и TTL. Catastrophic deny; Confirm — человек.
- SQLite: да, новая таблица через `SCHEMA` + `PRAGMA table_info`; удаление/expiry идемпотентно.
- Совместимость: feature flag default false; timeout даёт deny, а не allow.
- Источник: `reports/06-safety.md`. Оценка: 10–14 ч, П5/Р4.

### 7. Headless/scriptable login — только через возможности официального CLI

- Условие: pinned provider CLI выдаёт официальный two-step/no-browser flow; прямой обмен токенов
  HereAssistant не делает.
- Реализация: `manage_actions.py:152`, `manage_process.py:48`, `manage_config.py:16`, новый
  `manage_auth_flow.py`; pending state в `.runtime/pending-auth/` с TTL, без token/refresh token
  в SQLite. Старый visible TUI остаётся fallback.
- SQLite: нет при безопасном default; секретный pending-файл атомарный, каталог owner-only.
- Совместимость: новый режим opt-in; существующий login `manage_actions.py:167` не ломается.
- Источник: `reports/07-auth.md`. Оценка: 10–14 ч, П4/Р3.

### 8. Read-only каталог pi/OpenCode/Cursor

- Условие: владелец включает соответствующий surface; без provider/resume consumer не добавляем
  мёртвый parser.
- Реализация: расширить `core/session_import.py` и `chat_sessions.py:57`; OpenCode читает три
  каталога, Cursor — только transcript без subagents, pi — JSONL. `include_tools=False` навсегда
  по умолчанию.
- SQLite: нет; только ссылки/mtime в памяти процесса.
- Совместимость: additive dispatch, unknown provider возвращает пустой список.
- Источник: `reports/04-import.md`. Оценка: 6–10 ч, П2/Р2.

### 9. Плоская семантическая память на локальном ONNX

- Условия: решения владельца о доставке модели и хранении векторов; отдельный
  `agent.memory.semantic.enabled: true`; модель доступна до старта либо download явно разрешён.
- Реализация: новый `core/embeddings.py`; `core/agent_memory.py:208` делает hybrid lexical+cosine,
  fallback на нынешний lexical; `core/db.py:87` получает nullable `embedding BLOB`,
  `embedding_model`, `embedded_content_sha256`; `core/project_config.py:52` — новые fail-closed
  поля. Обновить docs/tests и все dependency/lock manifests.
- SQLite: да, nullable columns через `PRAGMA table_info`; backfill ленивый, транзакционный.
- Совместимость: старые rows и отсутствующая модель продолжают lexical path; re-embed только при
  несовпавшем model/hash.
- Источник: `reports/01-memory.md`. Оценка: 14–20 ч, П5/Р3.

### 10. Auto-poke, max-turn и confidence stepping

- Условия: явный project opt-in, лимит turn-ов и бюджета; принято решение о хранении confidence.
- Реализация: новые `core/auto_poke.py`, `core/confidence.py`; цикл вокруг
  `handlers/messages.py:337`, продолжение через returned session id; tool-managed append-only
  history, spike ≥15, max 2 turn по безопасному default. Synthetic poke не пишется как user message.
- SQLite: да только при persisted mode; `agent_todos/agent_todo_confidence`, scoped
  user+project+session. При memory-only mode миграции нет.
- Совместимость: default off; один обычный Telegram message по-прежнему делает один `prov.run`.
- Источник: `reports/08-ambient.md`. Оценка: 10–18 ч, П4/Р4.

### 11. Аутентифицированный live harness API

- Условие: есть внешний consumer, которому недостаточно текущих WebSocket status/log событий
  (`webapp/api/routes/ws.py:73`); определён минимальный v1 contract.
- Реализация: `core/harness_api.py` для frames/handshake/Unknown; live queues в provider boundary,
  routes в `webapp/api/server.py:174` под существующей auth; attach/cancel/soft input scoped
  user+session. Никакого неаутентифицированного Unix socket.
- SQLite: нет для live stream; history/peek разрешены только существующими project gates.
- Совместимость: отдельный `/api/v1/harness`, текущие `/ws` и tasks не меняются.
- Источник: `reports/05-harness-api.md`. Оценка: 16–20 ч, П4/Р3.

### 12. jcode как пятый provider — только с письменным разрешением

- Условие: владелец принимает ToS-риск, а поставщик письменно разрешает используемый auth/API
  режим; иначе пункт закрыт.
- Реализация: отдельный subprocess adapter без копирования identity/UA/remap; затрагивает
  `providers/__init__.py:11`, новый `providers/jcode.py`, `manage_config.py:16`,
  `core/native_sessions.py:18`, provider fixtures/docs.
- SQLite: нет, `accounts.provider` уже TEXT.
- Совместимость: additive registry; без configured account поведение не меняется.
- Источник риска: `reports/07-auth.md`. Оценка: 10–14 ч, П3/Р5.

### 13. LLM-extraction, cosine dedup и confidence decay памяти

- Условие: пункт 9 стабилен; пользователь выбрал LLM-канал и storage policy; default off.
- Реализация: `core/memory_extraction.py`, additions в `core/agent_memory.py:81`,
  trigger после turn в `handlers/messages.py:346`; Already-known prompt, категории/trust,
  threshold 0,85, prune/decay. Не извлекать git diffs, tool output, secrets.
- SQLite: да, category/trust/confidence/strength/reinforcements nullable либо отдельная
  scoped table; только идемпотентный schema path.
- Совместимость: Markdown source остаётся; auto-generated source отдельный и opt-in.
- Источник: `reports/01-memory.md`. Оценка: 12–20 ч, П3/Р4.

### 14. Реестр роя, task guard и reclaim

- Условие: подтверждён сценарий нескольких CLI в одном проекте и принят worktree/file-scope
  режим; default остаётся один active agent + queue.
- Реализация: новые `core/swarm.py`, `core/swarm_tasks.py`; schema рядом с
  `core/db.py:212`, status в existing WebSocket. Только metadata, без output tail/report body.
- SQLite: да, `swarm_members/swarm_tasks`, reclaim counter, user/project scope.
- Совместимость: feature flag off; hard cap калибруется для subprocess, не копируется 1000/32.
- Источник: `reports/02-swarm.md`. Оценка: 25–35 ч, П3/Р4.

### 15. Overnight/ambient scheduler

- Условия: пункты 5/6/10 готовы, есть единый rate-limit budget, владелец установил расходы,
  часы тишины и разрешённые действия.
- Реализация: `core/overnight.py`, `core/ambient_scheduler.py`, Telegram morning markdown;
  pause при active session, budget reserve ≥80%, max duration ≤72 ч, без push/payment/delete.
- SQLite: да, manifest/status/счётчики; prompt/log/diff только отдельными existing storage flags.
- Совместимость: default off, process restart восстанавливает только безопасный manifest.
- Источник: `reports/08-ambient.md`. Оценка: 20–28 ч, П2/Р5.

## Корзина 3 — не берём

Для всех пунктов ниже: файлы HereAssistant не затрагиваются, новые модули и миграции отсутствуют,
существующее поведение не меняется.

| Заимствование | Причина в одну строку |
|---|---|
| Прямой Anthropic/OpenAI OAuth/API с `claude-cli/1.0.0`, identity и remap | Имперсонация официального CLI создаёт неприемлемый ToS/account-risk; используем реальные CLI (`reports/07-auth.md`). |
| Snapshot + JSONL полного transcript | Дублирует SQLite/CLI session store и создаёт второй privacy-sensitive архив (`reports/09-runtime.md`). |
| Reactive context compaction | Историей и tool-loop владеет CLI, а HereAssistant передаёт только prompt/resume id (`reports/03-compaction.md`). |
| Граф памяти, BFS, clusters/cross-encoder | Цена и schema complexity преждевременны до метрик плоского hybrid search (`reports/01-memory.md`). |
| ACP/отдельный Unix-socket bridge | Нет consumer; текущий authenticated aiohttp лучше, а jcode socket не имеет достаточной auth (`reports/05-harness-api.md`). |
| Deep-swarm DAG/expand-node/critic gates | Не соответствует модели внешних CLI и не решает file conflicts (`reports/02-swarm.md`). |
| HTML/PNG productivity и review | Telegram достаточно Markdown; картинки/полные task cards расширяют privacy footprint (`reports/08-ambient.md`). |
| `output_tail`, completion reports, full tool logs в swarm/session state | Это содержимое диалога, запрещённое default-deny без отдельной необходимости (`reports/02-swarm.md`, `09-runtime.md`). |
| `tract-onnx`, tokio mpsc, Rust flock/ACL/hardlink как код | Это implementation-specific механика; переносим инварианты, не Rust-реализацию (`reports/01-memory.md`, `09-runtime.md`). |

## Решения владельца

| Решение | Безопасный default | Альтернатива и последствия |
|---|---|---|
| Как доставлять embedding model | Только офлайн: администратор кладёт versioned model+hash до включения. | Explicit first-run download: удобнее, но создаёт внешний запрос, supply-chain/availability риск; нужен UI-confirm, checksum и запрет silent retry. |
| Хранить ли vectors/confidence в `bridge.sqlite3` | Нет до project-level opt-in; lexical search остаётся. | Да: быстрее и возможен decay, но DB становится производным содержимым проекта; нужны scope, deletion/export, backup policy. Project-local sidecar уменьшает общий blast radius, но усложняет backup. |
| Добавлять ли jcode пятым provider | Нет. | Только с письменным разрешением и без identity/UA/remap. Иначе возможны блокировка подписок владельца и претензии по ToS. |
| Safety monitor или blocker | Monitor; raw command эфемерен, предупреждение post-factum/near-real-time. | Blocker только через проверенный pre-tool hook и human approval. Неполное покрытие provider-ов создаёт ложное чувство защиты; timeout должен deny. |
| Персистировать ли импортированные сессии | Нет, read-only каталог и resume из source file. | Копировать metadata только при `save_history`; message text только при `save_messages`; иначе появляется второй архив чужих harness transcript. |
| Включать ли auto-poke | Нет; обычный turn один. | Opt-in с max 2, budget cap и явным UI-индикатором; больше завершённых задач, но расход подписки и риск автономного side effect растут. |
| Где хранить pending auth | В credential store официального CLI; временно owner-only file с TTL. | SQLite даже с verifier/state расширяет секретный контур и backups; прямые access/refresh tokens в DB запрещены. |
| Разрешать ли несколько агентов в одном repo | Нет, один active turn + queue. | Только отдельный worktree/file scope и hard cap; без этого jcode-style task guard не предотвращает две правки одного файла. |
| Что делать с PowerShell/cmd risk | Помечать unsupported/Confirm, не заявлять защиту. | Писать отдельный Windows parser и fixtures; bash-правила нельзя молча применять к другой грамматике. |

## Порядок работ без сломанных состояний

1. Зафиксировать baseline: `scripts/quality_gate.sh`; записать текущие failures отдельно, не
   «чинить по пути» и не менять `uv.lock`.
2. Шесть Qwen-агентов ниже работают параллельно только в своих writable-зонах. Targeted tests
   идут параллельно; полный gate каждый запускает в выделенное координатором окно, чтобы шесть
   `uv sync` не спорили за одну `.venv`.
3. Применить завершённые зоны в порядке: agent 5 (cancel), agent 4 (native transcript), agent 3
   (resume), agent 6 (queue), agent 1 (classifier/UI), agent 2 (shell event adapter). После каждой
   зоны — полный gate. Agent 1 без agent 2 просто не получает `shell_calls`; agent 2 без agent 1
   только передаёт ephemeral optional field, поэтому оба промежуточных состояния рабочие.
4. Провести smoke matrix: Claude/Qwen/Gemini stream, Codex cancel/resume; private project без YAML,
   local memory off, Windows unsupported shell; затем ещё один полный gate.
5. Только после решений владельца брать корзину 2 строго сверху вниз; schema-changing пункты
   6/9/10/13/14/15 выполняются по одному, с fresh DB, legacy DB, повторным `db.init()` и rollback test
   (`tests/core/test_db_migrations.py:87`, `tests/core/test_db_migrations.py:199`).

## Готовые задания для шести Qwen-агентов

Общее для всех: рабочий корень — HereAssistant; `jcode/` и этот scratchpad только для чтения.
Нельзя менять файлы вне указанной writable-зоны. Нельзя добавлять зависимости. Финальная
обязательная проверка каждого задания — `scripts/quality_gate.sh`.

### Qwen 1 — классификатор и безопасное Telegram-уведомление

**Writable-зона:** `core/command_risk.py` (новый), `handlers/message_live.py`,
`tests/core/test_command_risk.py` (новый), `tests/handlers/test_message_live.py`.

**Запрещено:** любые `providers/**`, `core/db.py`, `handlers/messages.py`, manifests/lock/docs,
все прочие файлы.

**Сделать:** реализуй stdlib POSIX classifier по всем 20 правилам `reports/06-safety.md`.
API принимает command, cwd и optional home, возвращает typed level/findings без IO. В
`MessageLiveSession.progress_callback` обработай optional `meta["shell_calls"]`: классифицируй,
удали raw calls до присваивания `state.last_meta`, дедуплицируй alert в пределах turn и отправь
Confirm/Catastrophic без полного command/секретных аргументов. Safe/Low молчат; missing context и
PowerShell/cmd не получают ложный Safe. Если `shell_calls` отсутствует, поведение строго прежнее.

**Самопроверка:** unit tests всех правил, wrapper/chain/pipe/redirection, `$HOME`, temp, Windows
unsupported; handler tests доказывают отсутствие raw command в state и один alert. Затем targeted
pytest этих двух файлов и `scripts/quality_gate.sh`.

### Qwen 2 — transient shell calls в progress contract

**Writable-зона:** `providers/models.py`, `providers/parsers/claude.py`,
`providers/parsers/gemini.py`, `providers/claude_code.py`, `providers/qwen_code.py`,
`providers/gemini.py`, `tests/providers/test_claude_parser.py`,
`tests/providers/test_gemini_parser.py`, `tests/providers/test_claude_runtime.py`,
`tests/providers/test_qwen_runtime.py`, `tests/providers/test_gemini_runtime.py`.

**Запрещено:** `core/**`, `handlers/**`, Codex files, manifests/lock/docs, все прочие файлы.

**Сделать:** добавь optional `shell_calls` только в `ProgressMeta`, не в `ProviderMeta`.
Из Bash/PowerShell/run_shell_command tool input извлекай exact raw command плюс shell kind; runtime
добавляет cwd и best-effort home. Не клади calls в `provider_result`, logger, stderr message или
event dump. Unknown events остаются ignored. Существующие progress types и tuple contract не меняй.

**Самопроверка:** parser/runtime tests для Claude, Qwen, Gemini; докажи, что final meta не содержит
raw command, не-shell tool не создаёт call, broken/unknown event не падает. Затем targeted pytest
пяти разрешённых test-файлов и `scripts/quality_gate.sh`.

### Qwen 3 — read-only resume Claude/Codex

**Writable-зона:** `core/session_import.py` (новый), `chat_sessions.py`,
`tests/test_chat_sessions.py`, новые файлы только в `tests/fixtures/sessions/`.

**Запрещено:** остальные `core/**`, `providers/**`, `handlers/**`, DB/manifests/lock/docs,
все прочие файлы.

**Сделать:** сохрани публичный `ResumableSession`; вынеси safe parsers/catalog в новый модуль.
Добавь Codex dated JSONL catalog из выбранного `cli_home_path`, bounded top-20, max file size,
resolve-inside-home и broken-line tolerance. Из title исключи environment/AGENTS/service prompts,
tool/reasoning; image = `[image]`. Claude behavior сохрани, но проведи через общий safe extractor.
Никаких копий transcript и записей SQLite.

**Самопроверка:** fixtures для обоих provider-ов, malicious symlink/outside-home, broken JSON,
service title, top-N и stable session id; `pytest -q tests/test_chat_sessions.py`, затем
`scripts/quality_gate.sh`.

### Qwen 4 — privacy-safe native hook transcript

**Writable-зона:** `core/native_sessions.py`, `tests/core/test_native_sessions.py`.

**Запрещено:** все остальные файлы, особенно `core/db.py`, `core/crm_sync.py`,
`core/project_config.py`, manifests/lock/docs.

**Сделать:** замени `read_text().splitlines()` на bounded streaming read. Сделай рекурсивный
extractor с default `include_tools=False`: text survives unknown siblings, tool/reasoning excluded,
image/base64 becomes `[image]`. Не ослабляй resolve-inside-provider-home, 8 MiB, 20k и правило
«policy до open». Публичные сигнатуры/результаты не меняй.

**Самопроверка:** broken/unknown/tool/image/base64/oversize/symlink fixtures и тест, что при
выключенных prompt+message sync transcript вообще не открывается; targeted pytest, затем
`scripts/quality_gate.sh`.

### Qwen 5 — cancel/reap для Codex

**Writable-зона:** `providers/process.py`, `providers/base.py`,
`tests/providers/test_process.py`, `tests/providers/test_codex.py`.

**Запрещено:** stream-provider files, `core/**`, `handlers/**`, manifests/lock/docs,
все прочие файлы.

**Сделать:** добавь идемпотентный helper kill-and-reap и используй его в `_exec` при timeout и
`CancelledError`; cancel обязательно re-raise. Учти already-exited и `ProcessLookupError`.
Не меняй argv, env, public return tuple и timeout text без необходимости.

**Самопроверка:** fake process tests: normal exit, timeout, external cancellation, kill race,
wait exactly до reap; targeted pytest двух файлов, затем `scripts/quality_gate.sh`.

### Qwen 6 — один active turn и process-local queue

**Writable-зона:** `handlers/messages.py`, `handlers/message_state.py`,
`handlers/message_buffer.py`, `handlers/message_queue.py` (новый),
`tests/handlers/test_message_state.py`, `tests/handlers/test_message_buffer.py`,
`tests/handlers/test_message_queue.py` (новый).

**Запрещено:** `providers/**`, прочие handlers, `core/db.py`, config/project policy,
manifests/lock/docs, все прочие файлы.

**Сделать:** при `INTERRUPT_ON_NEW_MESSAGE=0` не создавай второй active task. Сложи подготовленный
ввод в process-local FIFO/coalescing queue по `ThreadKey`; после завершения/ошибки текущего turn
запусти следующий через существующий flow и актуальную conversation session. При `=1` сохрани
cancel behavior. Не персистируй prompt/пути, не создавай фонового бесконечного loop, очисти queue
при явной отмене пользователя.

**Самопроверка:** tests на 2–3 сообщения во время работы, exception/cancel текущего turn,
coalescing, разные users/threads и отсутствие двух одновременных `prov.run`; targeted pytest трёх
файлов, затем `scripts/quality_gate.sh`.
