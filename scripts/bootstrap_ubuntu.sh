#!/usr/bin/env bash
# Разовая подготовка HereAssistant на чистой Ubuntu (без Docker).
# Запуск из корня репозитория:  bash scripts/bootstrap_ubuntu.sh
# Идемпотентен: повторный запуск безопасен.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "== HereAssistant bootstrap (Ubuntu) в ${ROOT}"

# --- проверки системных зависимостей ---
need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ Нет '$1'. Установи: $2"; exit 1; }; }
need python3 "sudo apt install -y python3 python3-venv"
need node    "Node LTS: https://deb.nodesource.com или sudo apt install -y nodejs npm"
need npm     "sudo apt install -y npm"

python3 -c 'import venv' 2>/dev/null || { echo "❌ Нет python3-venv. sudo apt install -y python3-venv"; exit 1; }

# --- python venv + зависимости ---
if [ ! -d .venv ]; then
  echo "-- создаю .venv"
  python3 -m venv .venv
fi
echo "-- ставлю python-зависимости"
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

# --- фронтенд: зависимости + статическая сборка ---
echo "-- собираю фронтенд (npm ci + generate)"
( cd webapp/front && npm ci --no-audit --no-fund && npm run generate )

# --- runtime-каталоги + .env ---
echo "-- инициализирую runtime-каталоги и БД"
.venv/bin/python -c "from core import db; db.init(); print('   БД и каталоги готовы')"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "-- создан .env из .env.example — ЗАПОЛНИ TELEGRAM_BOT_TOKEN и ADMIN_IDS"
fi

echo
echo "== Готово. Дальше:"
echo "   1) отредактируй .env (TELEGRAM_BOT_TOKEN, ADMIN_IDS, WEBAPP_DOMAIN)"
echo "   2) залогинь CLI-провайдеры (см. docs/providers.md)"
echo "   3) pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api"
echo "   4) pm2 save && pm2 startup"
echo "   5) настрой nginx по docs/ubuntu-pm2-nginx.md (статика + прокси /api и /ws)"
echo "   Проверка: bash scripts/check_runtime.sh"
