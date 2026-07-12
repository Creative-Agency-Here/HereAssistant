# Production на Ubuntu: PM2 + nginx (без Docker)

Целевая схема: бот и API — PM2-процессы Python, фронтенд — статика Nuxt,
которую отдаёт nginx; `/api/` и `/ws` nginx проксирует на `127.0.0.1:8200`.

## 1. Системные пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-venv git nginx

# Node LTS (для сборки фронта и PM2)
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs

sudo npm i -g pm2
```

## 2. Код и bootstrap

```bash
git clone <repo-url> hereassistant && cd hereassistant
bash scripts/bootstrap_ubuntu.sh
```

Скрипт создаст `.venv`, поставит зависимости, соберёт фронт (`npm run generate`),
инициализирует `.runtime`/БД и скопирует `.env.example` → `.env`.

Заполни `.env`: `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `WEBAPP_DOMAIN`, при
необходимости `WEBAPP_ACCESS_KEY` (длинный случайный) и `SERVICE_API_TOKEN`.

## 3. CLI-провайдеры

Установи и авторизуй хотя бы один CLI: `claude`, `codex`, `gemini` —
см. `docs/providers.md`. Аккаунты добавляются через бота (`python manage.py`
или команды бота), auth-файлы живут в `.runtime/cli_homes/<аккаунт>/`.

## 4. PM2

```bash
pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api
pm2 save
pm2 startup   # выполни команду, которую он напечатает
pm2 status
```

Фронтенд-процесс в PM2 в проде НЕ нужен: статику отдаёт nginx (ниже).
`here-assistant-front-dev` — только для локальной разработки.

## 5. nginx

`/etc/nginx/sites-available/hereassistant` (замени `assistant.example.com`
и путь до репозитория):

```nginx
server {
    listen 80;
    server_name assistant.example.com;

    # Статика Nuxt (npm run generate)
    root /opt/hereassistant/webapp/front/.output/public;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # API → aiohttp на локальном порту
    location /api/ {
        proxy_pass http://127.0.0.1:8200;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # health без /api-префикса (для мониторинга)
    location = /health {
        proxy_pass http://127.0.0.1:8200;
    }

    # WebSocket живого статуса
    location /ws {
        proxy_pass http://127.0.0.1:8200;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/hereassistant /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# HTTPS (Telegram Mini App требует валидный сертификат)
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d assistant.example.com
```

## 6. Проверка

```bash
bash scripts/check_runtime.sh
scripts/quality_gate.sh   # перед release: lock, tests, types, lint, secrets/runtime hygiene

curl -s http://127.0.0.1:8200/health          # {"ok": true, ...}
curl -s http://127.0.0.1:8200/api/health
curl -s https://assistant.example.com/health   # через nginx

# сервисный API: без токена — 401/503, с токеном — 200
curl -i https://assistant.example.com/api/v1/tasks
curl -i -H "Authorization: Bearer $SERVICE_API_TOKEN" https://assistant.example.com/api/v1/tasks

pm2 logs here-assistant-api --lines 50
```

Напиши боту в Telegram — он должен ответить; `/web` откроет Mini App.

## Обновление

```bash
cd /opt/hereassistant
git pull
uv sync --frozen  # рекомендуемый воспроизводимый путь
# fallback без uv: .venv/bin/pip install -r requirements.txt -q
( cd webapp/front && npm ci --no-audit --no-fund && npm run generate )
pm2 restart here-assistant-bot here-assistant-api
```
