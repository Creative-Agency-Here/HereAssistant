# Contributing

Спасибо за интерес к HereAssistant. Короткие правила, чтобы PR проходили быстро.

## Локальная разработка

### Воспроизводимое окружение (рекомендуется)

```bash
uv sync --frozen
cp .env.example .env        # заполни TELEGRAM_BOT_TOKEN и ADMIN_IDS

uv run python bot.py
uv run python webapp/api/server.py
```

Для локального открытия Mini App без Telegram `initData` явно задай оба dev-флага:

```bash
HEREASSISTANT_ENV=development WEBAPP_DEV_SKIP_AUTH=1 uv run python webapp/api/server.py
```

Один `WEBAPP_DEV_SKIP_AUTH=1` в production авторизацию не отключает.

Локальная и поддерживаемая версия движка — Python 3.12 (CI: Ubuntu и Windows).
Python 3.10 исключён: locked `onnxruntime` не имеет cp310 wheel. Точные версии
зависимостей — в `uv.lock`.

### pip/venv fallback

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # заполни TELEGRAM_BOT_TOKEN и ADMIN_IDS
cd webapp/front && npm ci && cd ../..

# бот и API
.venv/bin/python bot.py
.venv/bin/python webapp/api/server.py
# фронт с HMR
cd webapp/front && npm run dev
```

На Ubuntu быстрее: `bash scripts/bootstrap_ubuntu.sh`, диагностика —
`bash scripts/check_runtime.sh`.

## Перед PR

```bash
scripts/quality_gate.sh
( cd webapp/front && npm run generate )   # фронт обязан собираться
bash scripts/check_runtime.sh
git status --short                        # никаких .env/.runtime/БД в диффе
```

## Жёсткие требования

1. **Никаких секретов и runtime-файлов в git**: `.env`, `bridge.sqlite3`,
   `.runtime/`, auth-файлы провайдеров, `node_modules`, сборки. `.gitignore`
   уже настроен — не ослабляй его.
2. **Privacy-first — не опция.** Любая новая фича, которая сохраняет или
   отправляет данные проекта (текст, диффы, пути, логи), обязана проходить
   через политику `core/project_config.py` и уважать default deny. Фичи,
   пишущие контент «по умолчанию», не принимаются. См. `docs/privacy.md`.
3. **Провайдеры остаются CLI-subprocess'ами** с изоляцией аккаунтов через
   `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `HOME`. Замена на прямые API-вызовы
   или общий auth-home — отдельное архитектурное обсуждение, не PR «мимоходом».
4. Не ломай существующие Telegram-команды и flow провайдеров; кроссплатформенность
   (Ubuntu основной путь, Windows — поддерживаемый legacy) сохраняем.
5. Ruff lint/format проверяют весь tree; Pyright расширяется ratchet-подходом.
   Broad exception debt контролирует `scripts/check_exception_ratchet.py`.

## Стиль

- Python: стандартная библиотека + существующие зависимости; новые пакеты —
  обсуждение в issue.
- Комментарии объясняют «почему», а не «что».
- Один PR — одна тема; в описании: что изменилось и как проверял.
