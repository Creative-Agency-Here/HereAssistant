# Отложенный план: семантическая память на локальном ONNX

**Статус: отложено 2026-07-29.** Решение и его основание — в
`docs/jcode-borrow-plan.md`, раздел «Решения по корзине 2».

Коротко: польза семантического поиска по памяти остаётся гипотезой — у нас нет
корпуса заметок, на котором лексический отбор доказанно проигрывает. Плюс работа
требует ручной офлайн-поставки модели администратором. Вместо неё сделана
блокировка разрушительных команд, у которой результат измерим.

План ниже готов к исполнению и сохранён целиком, чтобы не собирать его заново,
если появится измеримая потребность. Он составлен внешним консультантом (Codex)
и не проверялся построчно: перед запуском сверьте пути и номера строк с текущим
состоянием кода.

---

## Часть 2. План ближайшей волны

### Выбор

Взять только пункт 9 корзины 2: плоскую семантическую память на локальном ONNX.
Это максимальная оставшаяся польза при приемлемом риске: blocker остановлен отсутствием
pre-tool контракта, headless auth и auto-poke отклонены, import уже решён read-only,
а live API не имеет consumer. Точки расширения существуют: `core/agent_memory.py:208`,
`core/project_config.py:52`, `core/db.py:87`.

Не входят: download модели, confidence, extraction/dedup/decay, auto-poke, provider-ы
и изменение основного turn-а. При semantic-off, невалидном bundle, ошибке ONNX или
старой строке результат обязан совпадать с нынешним lexical path; сети нет никогда.

### Контракт волны

1. Включение требует `agent.memory.enabled: true`,
   `agent.memory.semantic.enabled: true`, `EMBEDDING_MODEL_DIR` и
   `EMBEDDING_MANIFEST_SHA256`. Отсутствующий/битый YAML остаётся `PRIVATE`
   (`core/project_config.py:134`).
2. Bundle: `manifest.json`, `model.onnx`, `tokenizer.json`. Env hash фиксирует байты
   manifest; schema 1 содержит `model_id`, `dimension`, `max_length`, `pooling: mean`,
   query/document prefixes и SHA-256 двух файлов. Symlink/outside-bundle либо mismatch
   дают `EmbeddingUnavailable` без download/retry.
3. Вектор — нормализованный little-endian float32 BLOB. В `agent_memory` добавляются
   только `embedding BLOB`, `embedding_model TEXT`,
   `embedded_content_sha256 TEXT`; confidence запрещён.
4. `MEMORY.md` остаётся первым. Для остальных lexical score нормализуется максимумом,
   cosine переводится из `[-1,1]` в `[0,1]`, итог:
   `0.35 * lexical + 0.65 * cosine`; проходят lexical `> 0` либо cosine `>= 0.35`.
   Tie-break: title, затем source id.
5. Lazy backfill — максимум 32 active rows за `select()`: вычисление вне
   write-транзакции, UPDATE compare-and-set по `content_sha256`. Изменение Markdown
   сразу обнуляет embedding-поля.
6. BLOB не попадает в prompt, Telegram, logs, CRM, WebSocket или export. При opt-out
   следующий `select()` очищает scoped-векторы; `/memory purge-semantic` — немедленно.

### Параллельность и порядок

Шесть агентов готовят изменения в отдельных worktree от одного baseline. Зоны файлово
не пересекаются, весь нужный контекст приведён в заданиях; scratchpad/jcode им не нужен.
Targeted tests и мутацию агент выполняет в worktree, затем rebase-ит зону на integration
head и сам запускает полный gate. Координатор применяет зоны строго `1 → 2 → 3 → 4 → 5 → 6`;
после каждой — `scripts/quality_gate.sh:1`, следующая зона ждёт зелёного результата.

Перед каждым mutation-run удалить `__pycache__` целевого package и tests; после
восстановления удалить снова. Центральная проверка должна сделать релевантный тест
красным, восстановленный код — зелёным. Полные gate запускать по одному, чтобы
`uv sync --frozen` (`scripts/quality_gate.sh:5`) не спорил за `.venv`.

### Зона 1 — зависимости и frozen lock

**Задание агенту.** Рабочий корень — HereAssistant. Код будет прямо импортировать
`onnxruntime`, `numpy`, `tokenizers`; сейчас они лишь транзитивны (`uv.lock:208`,
`uv.lock:459`, `uv.lock:758`).

**Разрешено:** `pyproject.toml`, `requirements.txt`, `uv.lock`,
`tests/test_project_metadata.py`. **Запрещено:** всё остальное, особенно `core/**`,
handlers, docs и model artifacts.

**Сделать:** одновременно объявить `numpy>=2.5,<3`, `onnxruntime>=1.27,<2`,
`tokenizers>=0.23,<1` в обоих manifests; пересобрать lock так, чтобы requirements
появились у root package (`uv.lock:285`), без обновления иных package versions.
Metadata test должен сверять names/specifiers во всех трёх местах.

**Проверка:** baseline gate; `uv lock`; `uv sync --frozen`;
`uv run --frozen pytest -q tests/test_project_metadata.py -p no:cacheprovider`;
`uv lock --check`. Мутация: удалить `onnxruntime` только из `requirements.txt`,
очистить test cache, получить красный metadata test; восстановить, очистить cache,
targeted test и полный gate.

**Провал:** manifests расходятся, lock обновляет чужие версии, появляется четвёртая
dependency, мутация зелёная либо gate красный.

### Зона 2 — fail-closed config

**Задание агенту.** Добавить независимые admin- и project-gates; ни один отсутствующий,
пустой или битый параметр не включает semantic memory.

**Разрешено:** `core/config.py`, `core/project_config.py`, `.env.example`,
`tests/core/test_project_config.py`, новый `tests/core/test_embedding_config.py`.
**Запрещено:** DB, memory/engine, handlers, manifests, docs и все иные файлы.

**Сделать:** optional `EMBEDDING_MODEL_DIR` и строго 64-hex
`EMBEDDING_MANIFEST_SHA256`, без чтения ФС при import. В `ProjectPolicy` добавить
`memory_semantic_enabled=False`, принимать только
`agent.memory.semantic.enabled: true`; helper true лишь при base memory, semantic и
валидной admin config. Global enable запрещён.

**Проверка:** missing/malformed YAML, integer `1`, bad hash, relative/empty path,
base-memory-off, полный double opt-in; targeted pytest двух файлов. Мутация: semantic
default=`True`; очистить `core/__pycache__`/test cache, доказать падение fail-closed test;
восстановить, targeted test и gate.

**Провал:** один флаг включает функцию, import читает ФС/сеть, bad config роняет startup,
старые policy semantics меняются, мутация/gate не проходят.

### Зона 3 — офлайн engine

**Задание агенту.** Создать engine, который знает только bundle и vectors, но не SQLite,
Telegram, policy или сеть.

**Разрешено:** новый `core/embeddings.py`, новый `tests/core/test_embeddings.py`.
**Запрещено:** все остальные файлы, binary fixture/model и любые HTTP/model-hub imports.

**Сделать:** typed `EmbeddingUnavailable`; проверка внешнего manifest hash, schema,
resolve-inside-bundle и hash model/tokenizer; затем CPU `InferenceSession` и tokenizer.
Реализовать batch encode, attention-mask mean pooling, L2 normalization,
dimension/finite checks, pack/unpack little-endian float32. Исключения не содержат
query/content.

**Проверка:** fake tokenizer/session: valid batch, empty, bad manifest/schema/file hash,
symlink escape, NaN, dimension, pooling, normalization, pack round-trip. Мутация:
обойти external manifest comparison; очистить cache, bad-hash test обязан упасть;
восстановить, targeted pytest и gate.

**Провал:** download, доверие hash из самого manifest без env anchor, content в log/error,
GPU-only/platform-dependent path, зелёная мутация либо красный gate.

### Зона 4 — SQLite schema

**Задание агенту.** Расширить `agent_memory` тремя nullable columns в стиле
`SCHEMA + PRAGMA table_info + ALTER` и атомарной migration (`core/db.py:328`).

**Разрешено:** `core/db.py`, `tests/core/test_db_migrations.py`. **Запрещено:**
все остальные файлы; versioned migration files и confidence columns.

**Сделать:** fresh schema и `MIGRATIONS` получают `embedding BLOB`,
`embedding_model TEXT`, `embedded_content_sha256 TEXT`. Legacy fixture должна иметь
старую `agent_memory` с row; доказать сохранность, NULL новых полей, повторный `init()`
и rollback всех ALTER при следующей намеренной ошибке.

**Проверка:** targeted migration pytest. Мутация: убрать `embedding_model` migration,
очистить `core/__pycache__`/test cache, legacy test обязан упасть; восстановить,
targeted pytest и gate.

**Провал:** потеря row, partial rollback, NOT NULL/backfill, confidence, неидемпотентный
init, зелёная мутация либо красный gate.

### Зона 5 — hybrid retrieval и lifecycle

**Задание агенту.** Подключить готовые `core.embeddings`, config/policy и DB columns к
памяти без изменения сигнатуры `select()` (`core/agent_memory.py:208`). До double
opt-in выдача должна быть прежним lexical path.

**Разрешено:** `core/agent_memory.py`, `tests/core/test_agent_memory.py`.
**Запрещено:** engine, config, DB, handlers, manifests, providers, docs и остальное.

**Сделать:** изменившийся content обнуляет embedding fields. Реализовать cached bundle,
prefixes, backfill ≤32 вне write transaction, CAS UPDATE по
`user_id+project_id+content_sha256`, формулу/tie-break из контракта. Ошибка engine,
bad BLOB или model/hash mismatch дают lexical fallback без content в log; opt-out
очищает только scope. Добавить `purge_embeddings()` и embedded count в `stats()`,
не возвращая BLOB.

**Проверка:** два users/projects; semantic-off/missing-model равны lexical; semantic-only
match, MEMORY-first, formula/tie, bound 32, stale CAS, content invalidation, bad
BLOB/model fallback, opt-out purge. Мутация: убрать `project_id` из purge predicate;
очистить cache, cross-project test обязан упасть; восстановить, targeted pytest и gate.

**Провал:** write без opt-in, vector/content наружу, cross-scope SQL, нет fallback,
unbounded backfill, обычная выдача изменилась, мутация/gate не проходят.

### Зона 6 — Telegram control и docs

**Задание агенту.** Показать безопасный semantic status в `/memory` и дать владельцу
немедленно удалить производный индекс. Команда не грузит модель и не показывает BLOB.

**Разрешено:** `handlers/system.py`, новый `tests/handlers/test_system_memory.py`,
`docs/privacy.md`, `docs/unified-agent-runtime.md`, `docs/unified-agent-runtime.ru.md`.
**Запрещено:** core, DB, providers, manifests/lock, README, WebApp и все иные файлы.

**Сделать:** `/memory` показывает `semantic: off/unavailable/ready`, model id и embedded
count без path/hash/content/vector. `/memory purge-semantic` доступна только
авторизованному user текущего accessible project, scoped и идемпотентна. Docs фиксируют
exact YAML/env, offline provisioning, отсутствие download, lexical fallback, backup
footprint, auto-purge при opt-out, explicit purge и отсутствие confidence.

**Проверка:** unauthorized, projectless, foreign project, три status, scoped/repeat
purge; docs examples совпадают с parser. Мутация: убрать accessible-project check;
очистить `handlers/__pycache__`/test cache, foreign-project test обязан упасть;
восстановить, targeted pytest и gate.

**Провал:** path/hash/content раскрыт, purge не scoped, unauthorized меняет DB, docs
обещают download, зелёная мутация либо красный gate.

### Финальная приёмка

После шести промежуточных gate — ещё один `scripts/quality_gate.sh` и CI Ubuntu/Windows
Python 3.12 (`.github/workflows/python-hardening.yml:13`). Smoke matrix: нет YAML,
битый YAML, только base memory, semantic без bundle, bad hash, valid bundle, смена
модели, opt-out/purge, два пользователя одного project.

Волна провалена при сетевом model request, write без double opt-in, cross-scope
read/purge, BLOB/content в telemetry, отличии lexical результата при semantic-off,
неатомарной migration или красном gate после любой зоны.
