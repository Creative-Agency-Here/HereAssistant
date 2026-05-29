# ТЗ — HereAssistant Mini App

**Версия:** 0.2 (Python self-host + Windows native)
**Дата:** 2026-05-28
**Авторы:** Илья + Личный ассистент Here

> Что изменилось с v0.1: ушли от FastAPI к aiohttp (он уже есть внутри aiogram, ничего нового ставить не нужно), убрали Docker (Windows нативно), добавили multi-tenant подход чтобы продукт можно было отдать другим людям.

---

## 1. Зачем это

Сейчас HereAssistant живёт в Telegram-чате. Это удобно для коротких задач, но плохо для трёх вещей:

1. **Не видно истории «свысока»** — что и когда правилось, какие проекты, сколько токенов потрачено, где упало.
2. **Длинные ответы и логи** скроллятся плохо, теряются в чате.
3. **Нет панели управления** — переключиться между аккаунтами, прервать задачу, посмотреть статус всех проектов — нужно знать команды и слать их боту.

Mini App — это веб-приложение внутри Telegram (открывается кнопкой из бота, авторизация автоматическая по Telegram-аккаунту).

**Главная задача**: видеть всё что делает ассистент, в красивом виде, прямо из Telegram, без необходимости лезть в файлы на сервере.

**Дополнительная задача**: продукт изначально проектируется как **self-host**, чтобы можно было раздать/продать другим людям. Один git clone + `install.bat` — и заводится у любого на Windows-сервере.

---

## 2. Кто пользователь и сценарии

**Пользователи:**
- **MVP**: один владелец инстанса (Илья, Telegram-ID указывается в `.env`).
- **Дальше**: несколько админов в одном инстансе (список `ADMIN_IDS` в `.env`).
- **Не в MVP**: публичная регистрация, тарифы, биллинг.

**Главные сценарии (по убыванию частоты):**

1. **«Что ассистент делает сейчас?»** — посмотреть текущую активную задачу: проект, модель, шаг, секундомер, кнопка «Прервать».
2. **«Что я просил вчера/неделю назад?»** — открыть историю диалогов, найти запрос, прочитать ответ, скопировать кусок кода.
3. **«Какие правки были в проекте X?»** — список правок файлов с diff-ом, фильтр по проекту, поиск по тексту.
4. **«Сколько потратил токенов / денег за месяц?»** — статистика по аккаунтам, моделям, времени, проектам.
5. **«Что в логах за вчера?»** — открыть `bot.log`, фильтр по дате/уровню/проекту.
6. **«Откатить вчерашнее изменение»** — кнопка «откатить» рядом с правкой файла → ассистент применяет git revert или прямую обратную правку.
7. **«Запустить запланированное действие»** — типа «сделать скриншоты сайта iSfera24» одной кнопкой, без печатания.

---

## 3. Что войдёт в первую версию (MVP)

### 3.1 Экран «Сейчас»
- Активная задача: проект, текст запроса, текущий шаг, секундомер, кнопка «Прервать».
- Аккаунт + модель + заметки.
- Последние 5 действий (Read X, Edit Y, PowerShell Z).
- Лог-стрим внизу (последние 50 строк `bot.log`, обновление каждые 2 секунды через WebSocket).

### 3.2 Экран «История»
- Список диалогов по дате (из `bridge.sqlite3`).
- Поиск по тексту запроса.
- Фильтр: проект (cwd), аккаунт, модель, период.
- Карточка диалога: запрос → ответ → шаги ассистента → правки файлов.
- Кнопка «Открыть в чате» — прыжок в Telegram на тот тред.

### 3.3 Экран «Правки»
- Список всех правок файлов с временной шкалой.
- Фильтр: проект, тип (Edit/Write/MultiEdit), файл.
- Превью diff в стиле GitHub (зелёное/красное).
- Кнопка «Откатить» (для одной правки или цепочки).
- Кнопка «Открыть файл».

### 3.4 Экран «Статистика»
- Графики: токены/день, время/день, рестарты/день.
- Топ-10 файлов по числу правок.
- Топ-5 моделей по использованию.
- Расход по аккаунтам.

### 3.5 Экран «Настройки»
- Список аккаунтов — выбор активного.
- Список моделей — переключение.
- Кнопки старта запланированных задач.
- Пороги для длинного текста / шагов.

### 3.6 НЕ в MVP (на потом)
- Тарифы / биллинг / публичная регистрация.
- Управление воркфлоу (создание новых проектов/аккаунтов через UI).
- Голосовой ввод в Mini App (через бот в Telegram уже есть).
- Отдельная мобильная версия (Mini App адаптивен).

---

## 4. Архитектура

```
┌─────────────────┐   user opens Mini App
│  Telegram app   │ ─────────────────────────┐
└─────────────────┘                          ▼
                                  ┌──────────────────────┐
                                  │   Mini App (Nuxt 3)  │
                                  │   webapp/front/.output
                                  │   статика, nginx     │
                                  └──────────┬───────────┘
                                             │ REST + WebSocket
                                             ▼
                                  ┌──────────────────────┐
                                  │  Web API (aiohttp)   │
                                  │  Python 3.12         │
                                  │  port 8200           │
                                  │  pm2 process         │
                                  └──────────┬───────────┘
                                             │ читает/пишет
                                             ▼
                                  ┌──────────────────────┐
                                  │  bridge.sqlite3      │
                                  │  + .runtime/state    │
                                  │  + .runtime/logs     │
                                  └──────────┬───────────┘
                                             ▲
                                             │ тот же файл/БД
                                  ┌──────────┴───────────┐
                                  │  HereAssistant bot   │
                                  │  bot.py, aiogram     │
                                  │  pm2 process         │
                                  └──────────────────────┘
```

**Главные принципы:**

1. **Один язык — Python 3.12 везде**. Бот и API — два процесса одного стека.
2. **Один источник данных — `bridge.sqlite3`**. Никакой синхронизации, никаких очередей между ботом и API в MVP. Оба процесса читают и пишут один файл (SQLite держит блокировки сама).
3. **Veб-сервер — aiohttp**, который уже идёт зависимостью к `aiogram`. Ничего нового ставить не нужно.
4. **Redis — опционально, не в MVP**. Для realtime-стрима логов в MVP хватит чтения хвоста `bot.log` каждые 2 сек. Если в будущем понадобится pub/sub между несколькими процессами — поставим Memurai (Windows-порт Redis) и подключим `redis-py`.
5. **Self-contained**: всё в одном репозитории `HereAssistant\`, никаких внешних сервисов кроме nginx (статика) и Python (бот + API).

---

## 5. Стек

| Слой | Технология | Зачем |
|---|---|---|
| Бот | Python 3.12 + aiogram 3.13 | Уже есть |
| Web API | Python 3.12 + aiohttp | Уже в зависимостях aiogram, ничего ставить не надо |
| WebSocket | aiohttp.web.WebSocketResponse | Встроено в aiohttp |
| Авторизация | Telegram initData (HMAC по bot-token) | Стандарт Mini App |
| База | SQLite (`bridge.sqlite3`) | Уже есть |
| Кеш/очереди | Не нужны в MVP | (Memurai позже если понадобится) |
| Фронт | Nuxt 3 SSG + Tailwind v3 + Naive UI | Как остальные сайты Ильи |
| Шрифты | Bebas Neue + Core Sans (local) | Единый бренд |
| Графики | Chart.js | Лёгкий, хватит для MVP |
| Diff | diff2html | Готовый GitHub-style рендер |
| Веб-сервер | nginx for Windows | Статика + reverse proxy на :8200 |
| SSL | Win-ACME (Let's Encrypt) | Бесплатный сертификат |
| Process manager | PM2 (через `--interpreter python`) | Один менеджер для бота и API |
| ОС | Windows Server 2022 нативно | Без Docker, без WSL |

---

## 6. Структура проекта

```
C:\Users\Administrator\Desktop\HereAssistant\
├── bot.py                    # как сейчас, без изменений
├── handlers/                 # как сейчас
├── core/                     # как сейчас
├── webapp/
│   ├── api/                  # aiohttp веб-сервер
│   │   ├── server.py         # точка входа
│   │   ├── auth.py           # проверка Telegram initData
│   │   ├── routes/
│   │   │   ├── now.py        # /api/now
│   │   │   ├── history.py    # /api/history
│   │   │   ├── edits.py      # /api/edits
│   │   │   ├── stats.py      # /api/stats
│   │   │   ├── settings.py   # /api/settings
│   │   │   ├── logs.py       # /api/logs
│   │   │   └── ws.py         # /ws (realtime)
│   │   └── repo.py           # обёртка над core.db для веб-запросов
│   └── front/                # Nuxt 3 SSG
│       ├── nuxt.config.ts
│       ├── pages/
│       │   ├── index.vue     # «Сейчас»
│       │   ├── history.vue
│       │   ├── edits.vue
│       │   ├── stats.vue
│       │   └── settings.vue
│       ├── components/
│       ├── composables/
│       │   ├── useApi.ts     # обёртка fetch с initData в Authorization
│       │   └── useWebSocket.ts
│       └── public/fonts/
├── ecosystem.config.js       # PM2: bot + api (новый файл)
├── install.bat               # одношаговая установка для нового пользователя
├── update.bat                # git pull + npm install + nuxt generate + pm2 restart
└── .env.example              # все переменные с пояснениями
```

---

## 7. Структура данных

**Что есть в `bridge.sqlite3`:**
- `conversations` — диалоги по chat_id+thread_id
- `messages` — сообщения юзера и ассистента
- `accounts` — аккаунты (claude_hus, gemini__5 и т.д.)
- `events` — журнал действий

**Что добавляется в v0.2:**

- `file_edits` — каждая правка файла. Колонки: `id, ts, conv_id, owner_user_id, path, tool, added, removed, old_snippet, new_snippet, reverted_at`.
- `actions` — пресет-действия. Колонки: `id, owner_user_id, name, description, command, cwd, last_run_at, last_status`.
- `restart_history` — все рестарты с diff (сейчас в `restart_count.json` + `snapshot_full.json`). Перенос в БД.
- `users` — Telegram-юзеры с доступом. Колонки: `tg_id, username, name, role (owner|admin|viewer), added_at`.
- `webapp_sessions` — короткоживущие сессии Mini App. Колонки: `session_id, tg_id, init_data_hash, expires_at`.

Все таблицы создаются автомиграцией при первом запуске. Существующие данные не теряются.

**Поле `owner_user_id` везде** — для будущей мульти-арендности. В MVP у всех правок будет один owner = первый пользователь из `ADMIN_IDS`.

---

## 8. Внутренние адреса (API endpoints)

| Метод | Адрес | Что делает |
|---|---|---|
| GET | `/api/now` | Текущая активная задача + последние 5 действий |
| GET | `/api/history` | Список диалогов с фильтрами |
| GET | `/api/history/{conv_id}` | Один диалог целиком |
| GET | `/api/edits` | Список правок |
| GET | `/api/edits/{edit_id}` | Полный diff одной правки |
| POST | `/api/edits/{edit_id}/revert` | Откатить правку |
| GET | `/api/stats` | Агрегаты для графиков |
| GET | `/api/settings` | Текущие настройки + аккаунты + список пользователей |
| POST | `/api/settings/account` | Сменить активный аккаунт |
| GET | `/api/actions` | Список пресет-действий |
| POST | `/api/actions/{id}/run` | Запустить действие |
| GET | `/api/logs` | Хвост `bot.log` |
| WS  | `/ws` | Стрим: статус задачи, логи, события |

Каждый запрос проверяет Telegram initData в заголовке `Authorization: tma <initData>`.

---

## 9. Безопасность

- **`ADMIN_IDS` в `.env`** — список Telegram-ID с доступом. Любой другой пользователь — 403.
- **Никаких хардкодов admin_id в коде** — только через переменную окружения.
- **initData валиден 1 час** (стандарт Telegram).
- **HMAC-проверка initData** по бот-токену — стандартная функция из доки Telegram (~30 строк кода).
- **HTTPS обязателен** (Mini App в Telegram открывает только https).
- **API не лезет за пределы корня проекта** при просмотре файлов и откатах — путь нормализуется и проверяется `Path.resolve()` против белого списка.
- **«Откатить правку»** — двойное подтверждение в UI + запись в `events` с типом `revert` и id того кто откатывал.
- **CORS:** API разрешает только домен Mini App (`WEBAPP_DOMAIN` в `.env`), никаких `*`.
- **Rate limiting** на API — 60 запросов/мин на одного юзера (через простой in-memory счётчик, для self-host этого хватит).

---

## 10. Деплой на Windows нативно

### Что должно быть на сервере заранее:
- Windows Server 2022 (или Windows 10/11)
- Python 3.12 (системный установщик)
- Node.js 18+ (для билда фронта)
- nginx for Windows
- PM2 (`npm install -g pm2`)
- Win-ACME (для SSL Let's Encrypt)

### Один шаг установки для нового пользователя:

```cmd
git clone https://github.com/<user>/here-assistant.git
cd here-assistant
install.bat
```

Что делает `install.bat`:
1. `pip install -r requirements.txt` (Python зависимости)
2. `cd webapp\front && npm install && npm run generate` (билд фронта)
3. Создаёт `.env` из `.env.example` если нет, открывает в блокноте для заполнения
4. Прописывает nginx-конфиг в `C:\nginx\conf\sites\here-assistant.conf`
5. `pm2 start ecosystem.config.js` — запускает бота и API
6. `pm2 save` + `pm2 startup` — автозапуск при перезагрузке Windows
7. Подсказывает: «Открой `https://<твой-домен>` и нажми кнопку Mini App в боте».

### `.env.example`:
```env
# Telegram
TELEGRAM_BOT_TOKEN=
ADMIN_IDS=123456789           # через запятую если несколько

# Web
WEBAPP_DOMAIN=panel.example.ru
WEBAPP_PORT=8200

# Аккаунты CLI (имена)
DEFAULT_ACCOUNT=claude_main

# Лимиты
LONG_TEXT_LIMIT=3500
LONG_STEPS_LIMIT=15
```

### `ecosystem.config.js` (PM2):
```js
module.exports = {
  apps: [
    {
      name: 'here-assistant-bot',
      script: 'bot.py',
      interpreter: 'python',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: 'here-assistant-api',
      script: 'webapp/api/server.py',
      interpreter: 'python',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
    },
  ],
};
```

### nginx-конфиг (фрагмент):
```nginx
server {
    listen 443 ssl http2;
    server_name panel.example.ru;

    ssl_certificate     C:/win-acme/certs/panel.example.ru.crt;
    ssl_certificate_key C:/win-acme/certs/panel.example.ru.key;

    root C:/Users/Administrator/Desktop/HereAssistant/webapp/front/.output/public;
    index index.html;

    location /api/ { proxy_pass http://127.0.0.1:8200; }
    location /ws   { proxy_pass http://127.0.0.1:8200; proxy_http_version 1.1;
                     proxy_set_header Upgrade $http_upgrade;
                     proxy_set_header Connection "upgrade"; }
    location /     { try_files $uri $uri/ /index.html; }
}
```

---

## 11. Этапы реализации

**Этап 1 — Фундамент (1-2 дня)**
- Поднять aiohttp на 8200, заглушка на `/api/now`.
- Авторизация по initData.
- Скелет Nuxt 3 SSG, одна страница «Сейчас» с фейковыми данными.
- `ecosystem.config.js` для PM2 (бот + API).
- nginx + поддомен + SSL.
- `install.bat` v1 (без полной автоматизации, базовая).

**Этап 2 — «Сейчас» + «История» (2-3 дня)**
- Реальные данные из `bridge.sqlite3`.
- WebSocket для realtime-логов (без Redis, просто tail файла).
- Кнопка «Прервать задачу» (через флаг в SQLite, бот его опрашивает).

**Этап 3 — «Правки» (2-3 дня)**
- Таблица `file_edits` + миграция из events.
- UI с diff (через `diff2html`).
- Кнопка «Откатить» (сначала только для Edit, потом Write).

**Этап 4 — «Статистика» (1-2 дня)**
- Графики через Chart.js.
- Агрегаты по периодам.

**Этап 5 — «Настройки» + действия (2 дня)**
- Управление аккаунтами/моделями.
- Таблица `actions`, запуск через бота.

**Этап 6 — Полировка и упаковка (2-3 дня)**
- Тёмная/светлая тема (по `Telegram.WebApp.colorScheme`).
- Адаптив под мобильный Telegram.
- `install.bat` финальный с автонастройкой nginx.
- `README.md` для self-host.
- Бренд (нейтральное название вместо «HereAssistant»).

**Итого: 2-3 недели** при работе по полдня. MVP-минимум (этап 1+2) — **3-5 дней**.

---

## 12. Открытые вопросы (нужен ответ)

1. **Бренд / название продукта**: если делаем для других — нужно нейтральное имя. Варианты: `TGAssist`, `BotMate`, `Pilot`, `Helmsman`, `ClaudeKit`. Свой вариант?
2. **Поддомен**: для твоего инстанса — `panel.hereagency.ru`? `assistant.hereagency.ru`? Что-то ещё?
3. **Дизайн**: придерживаться стиля `hereagency.ru` (Bebas + Core Sans) или сделать более «технический» вид как у GitHub/Linear?
4. **Тёмная тема**: нужна сразу или потом?
5. **Откат правок**: через `git revert` (если проект под git) или прямую обратную Edit-операцию?
6. **MVP-минимум**: какие из 5 экранов точно нужны на старте? Я бы оставил только «Сейчас» + «История» — этого хватит чтобы понять полезность.
7. **Лицензия для self-host**: MIT (раздаёшь свободно), proprietary (продаёшь), AGPL (форк должен оставаться открытым)?
8. **Распространение**: GitHub public / GitHub private с продажей доступа / просто архив друзьям?
9. **Голосовой ввод в Mini App** — нужен или хватит того что в боте?
10. **Realtime-логи без Redis в MVP**: окей если задержка ~2 сек (читаем хвост файла), или сразу строить через Memurai+pub/sub?

---

## 13. Чего я НЕ обещаю

- Работу на Linux/Mac «из коробки» — целевая платформа MVP только Windows. Linux-поддержка добавляется отдельной задачей.
- Кросс-инстансную синхронизацию (один аккаунт на нескольких серверах).
- Работу без интернета — Mini App это веб-приложение.
- Mobile-приложение в App Store / Google Play — Mini App открывается внутри Telegram.
- Авто-обновления продакшена — `update.bat` руками или через cron-задачу.
- Полную замену CLI/чата — Mini App дополнение, не замена.

---

## 14. Что меняется относительно v0.1

| Раздел | Было | Стало |
|---|---|---|
| Бэк | FastAPI | **aiohttp** (уже в зависимостях aiogram) |
| Redis | обязателен | **опционально**, не в MVP (хватит SQLite + tail файла) |
| Деплой | Docker Compose | **install.bat** + PM2 + nginx Windows |
| Архитектура | Mini App встроен в твою инфру | **Self-contained продукт**, можно отдать другим |
| Авторизация | hardcoded `admin_id` | **ADMIN_IDS** список в `.env` |
| Структура | расчёт на тебя одного | мульти-юзер с самого начала (поле `owner_user_id`) |
| ОС | без явных требований | **Windows нативно**, без Docker/WSL |
| Платформа поддержки | Linux/Mac/Win | Windows только в MVP, Linux позже |

---

*Это второй черновик. Особо нужны ответы на раздел 12 — без них точное планирование этапов не имеет смысла.*
