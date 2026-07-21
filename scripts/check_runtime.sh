#!/usr/bin/env bash
# Sanity-проверка окружения HereAssistant: CLI-провайдеры, .env, БД, сборка фронта.
# Запуск из корня репозитория:  bash scripts/check_runtime.sh
set -uo pipefail

cd "$(dirname "$0")/.."
FAIL=0

ok()   { echo "  ✅ $1"; }
warn() { echo "  ⚠️  $1"; }
bad()  { echo "  ❌ $1"; FAIL=1; }

echo "== CLI-провайдеры (нужен хотя бы один)"
FOUND_CLI=0
for cli in claude codex gemini qwen; do
  if command -v "$cli" >/dev/null 2>&1; then
    ok "$cli: $(command -v "$cli")"
    FOUND_CLI=1
  else
    warn "$cli не найден в PATH"
  fi
done
[ "$FOUND_CLI" = "1" ] || bad "не найден ни один CLI-провайдер (claude/codex/gemini/qwen)"

echo "== Python и зависимости"
if [ -x .venv/bin/python ]; then
  ok ".venv найден"
  .venv/bin/python -c "import aiogram, aiohttp, yaml" 2>/dev/null \
    && ok "aiogram/aiohttp/PyYAML импортируются" \
    || bad "python-зависимости не стоят: .venv/bin/pip install -r requirements.txt"
else
  bad "нет .venv — запусти bash scripts/bootstrap_ubuntu.sh"
fi

echo "== Конфигурация"
if [ -f .env ]; then
  ok ".env существует"
  grep -q "^TELEGRAM_BOT_TOKEN=..*" .env && ok "TELEGRAM_BOT_TOKEN заполнен" || bad "TELEGRAM_BOT_TOKEN пуст"
  grep -q "^WEBAPP_DEV_SKIP_AUTH=1" .env && bad "WEBAPP_DEV_SKIP_AUTH=1 — ЗАПРЕЩЕНО в проде" || ok "dev skip-auth выключен"
else
  bad "нет .env (cp .env.example .env)"
fi

echo "== База данных"
if [ -x .venv/bin/python ]; then
  .venv/bin/python -c "from core import db; db.init()" 2>/dev/null \
    && ok "БД инициализируется" || bad "ошибка инициализации БД"
fi

echo "== Фронтенд (статическая сборка для nginx)"
if [ -d webapp/front/.output/public ]; then
  ok "webapp/front/.output/public существует"
else
  warn "нет статической сборки — cd webapp/front && npm ci && npm run generate"
fi

echo
[ "$FAIL" = "0" ] && echo "== Всё критичное на месте" || { echo "== Есть проблемы (см. ❌ выше)"; exit 1; }
