# HereAssistant Mini App

Веб-приложение для управления HereAssistant через Telegram Mini App.

## Структура

- `api/` — aiohttp веб-сервер (Python 3.12), читает `bridge.sqlite3`
- `front/` — Nuxt 3 SSG (Tailwind, тёмная тема, Telegram WebApp SDK)

## Локальная разработка

```cmd
:: 1) Поднять API
set WEBAPP_DEV_SKIP_AUTH=1
python webapp\api\server.py
:: → http://127.0.0.1:8200

:: 2) В другом окне — Nuxt dev
cd webapp\front
npm run dev
:: → http://localhost:3000
```

Открой `http://localhost:3000` — увидишь экран «Сейчас». Авторизация
отключена флагом `WEBAPP_DEV_SKIP_AUTH=1`, в проде она строго проверяется.

## Готовые ручки

| Метод | Адрес | Что отвечает |
|---|---|---|
| GET | `/api/health` | `{ok: true, version}` |
| GET | `/api/now` | Активная задача + последние 5 действий |
| GET | `/api/history?limit=20&offset=0&q=...` | Список диалогов |
| GET | `/api/history/{conv_id}` | Один диалог с сообщениями |
| WS  | `/ws` | Стрим логов (`log_init`, `log_append`) + статус (`status`) каждые 2 сек |

## Прод-сборка фронта

```cmd
cd webapp\front
npm run generate
:: статика в .output\public — копировать в nginx
```

## Environment

| Переменная | Default | Что |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Для HMAC-проверки initData |
| `ADMIN_IDS` | — | Список Telegram-ID через запятую (есть доступ к Mini App) |
| `ADMIN_TELEGRAM_ID` | — | Легаси, тоже учитывается |
| `WEBAPP_PORT` | 8200 | Порт API |
| `WEBAPP_HOST` | 127.0.0.1 | Хост API |
| `WEBAPP_DOMAIN` | — | Прод-домен Mini App, для CORS (`https://...`) |
| `WEBAPP_DEV_SKIP_AUTH` | 0 | dev: пропускать initData |
| `WS_TICK_SEC` | 2.0 | Период обновления статуса в WebSocket |
