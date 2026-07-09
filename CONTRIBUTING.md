# Contributing

Спасибо за интерес к HereAssistant. Короткие правила, чтобы PR проходили быстро.

## Локальная разработка

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
.venv/bin/python -m compileall bot.py core handlers providers utils webapp/api
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

## Стиль

- Python: стандартная библиотека + существующие зависимости; новые пакеты —
  обсуждение в issue.
- Комментарии объясняют «почему», а не «что».
- Один PR — одна тема; в описании: что изменилось и как проверял.
