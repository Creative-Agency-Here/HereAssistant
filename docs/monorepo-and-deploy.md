# HereAssistant — монорепо и деплой (как работать дальше)

## Решение
**Один репозиторий (монорепо).** Бэк и фронт вместе — новый разработчик клонирует и поднимает локально весь стек: бот + API + вебап. Граница проведена **по API**, чтобы при желании потом легко вынести фронт в отдельный (закрытый) репо без переделки.

## Структура репозитория
```
HereAssistant/                       # один репо = весь продукт
├── bot.py, core/, providers/, handlers/, utils/   # Python — ДВИЖОК (бот, раннер агентов)
├── webapp/
│   ├── api/                         # Python — WEB API (контракт между фронтом и движком)
│   └── front/                       # JS — ФРОНТ (после редизайна: Vite + Vue 3 + Naive UI)
│       ├── public/fonts/            # Core Sans / Bebas (копия из агентского монорепо)
│       ├── src/theme/               # тема Naive UI (glass, акцент #AB60F6)
│       └── ...
├── ecosystem.config.js              # PM2: бот + api (на DE-1)
├── scripts/deploy_web.py            # сборка фронта + rsync на сервер (по образцу агентского)
├── requirements.txt                 # Python-зависимости (бот + api)
├── README.md
└── .gitignore                       # .env, .runtime, bridge.sqlite3, node_modules — не коммитим
```
Три части продукта: **движок → API → фронт**. API — это граница.

## Как разработчик запускает локально
```bash
git clone <repo> && cd HereAssistant

# 1) движок + API (Python)
pip install -r requirements.txt
cp .env.example .env          # вписать TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_ID
python bot.py                 # бот
set WEBAPP_DEV_SKIP_AUTH=1 && python webapp/api/server.py   # API :8200 (без авторизации локально)

# 2) фронт (JS)
cd webapp/front
pnpm install
pnpm dev                      # :3000, ходит в localhost:8200
```
Локально всё на одной машине: фронт бьёт в `127.0.0.1:8200`, авторизация отключена флагом. Новый дев видит и запускает **весь стек** из одного репо.

## Как ТЫ деплоишь (два независимых деплоя — граница по API)

**1. Движок + API → DE-1** (там бот и живая база `bridge.sqlite3`):
- под PM2: `pm2 restart here-assistant-bot here-assistant-api` (или `/deploy` в боте).
- Это **ядро. Едет само, фронт ему не нужен** — вот ответ на «как заливать CLI-продукт без фронта».

**2. Фронт → сервер статики** (RU или DE-1):
- `python scripts/deploy_web.py` → `pnpm build` → `rsync dist/` на `/var/www/assistant.hereagency.ru/`.
- В проде фронт бьёт в `https://api-assistant.hereagency.ru` (это API на DE-1).

## Деплой-скрипт (основа взята с агентского фронта)
`scripts/deploy_web.py` повторяет логику `Sites/HereAgency/scripts/deploy.py`:
1. чистит vite-кэш (`node_modules/.vite`)
2. `pnpm install --frozen-lockfile`
3. `pnpm build` → `dist/`
4. `rsync -az --delete dist/ root@<server>:/var/www/assistant.hereagency.ru/`

Запускается с **Unix-машины** (твой Mac) или прямо на сервере — там, где есть `rsync` + ssh-доступ (на DE-1/Windows rsync нет, поэтому деплой фронта — не с него).

## Прод-связка, если фронт на RU
- DNS: `assistant.hereagency.ru` → **RU** (фронт), `api-assistant.hereagency.ru` → **DE-1** (API).
- На DE-1: сертификат `api-assistant` + nginx-прокси на `127.0.0.1:8200` + CORS для origin фронта. initData-авторизация уже есть.
- Фронт собран с `apiBase = https://api-assistant.hereagency.ru`.
- **Проще:** если фронт оставить на DE-1 (как сейчас) — без CORS и без поддомена. RU нужен только если важна скорость первой загрузки.

## Твой рабочий цикл дальше
1. Правишь код (бот / api / front) в **одном** репо.
2. Бэк — рестарт PM2 на DE-1 (или `/deploy`).
3. Фронт — `python scripts/deploy_web.py` → статика на сервер.
4. Коммит/пуш в Gitea → общая история, новые девы видят весь стек.

## Как HereAssistant отличает push от deploy

Git-синхронизация и production-деплой — разные факты. HereAssistant показывает
dirty/ahead/behind напрямую из Git, но не называет commit задеплоенным без маркера
от штатного deploy-hook. Необязательный файл проекта:

```json
{
  "deployedAt": "2026-07-21T12:00:00+03:00",
  "targets": {
    "admin": { "commit": "0123456789ab", "status": "deployed" },
    "site": { "commit": "0123456789ab", "status": "deployed" }
  }
}
```

Путь: `.hereassistant/deploy-state.json`. Если его нет, UI честно пишет «нет
подтверждения деплоя». Если commit совпадает только у части targets — «частично»;
если не совпадает ни у одного — «ожидает деплоя». Сам файл должен обновлять deploy
script/hook после успешной production-проверки, а не разработчик вручную.

## Что отдавать разработчикам
- **Сейчас:** весь монорепо — видят движок и фронт, запускают всё. Максимальная скорость онбординга.
- **Когда захочешь закрыть фронт:** выносишь `webapp/front` в отдельный приватный (агентский) репо, девам остаётся `ядро + API`. Граница уже по API — вынос **без переделки кода**.
