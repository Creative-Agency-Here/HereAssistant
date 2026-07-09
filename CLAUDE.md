# ЯЗЫК ОБЩЕНИЯ

Общайся с пользователем **только на русском**. Комментарии в коде — на русском
(сложившийся стиль кодовой базы). Публичные OSS-документы — двуязычно:
`README.md` (EN, основной) ↔ `README.ru.md`; новые пользовательские доки
для внешней аудитории пиши на английском или парой EN/RU.

---

# СТАТУС ПРОЕКТА: ПУБЛИЧНЫЙ ОПЕНСОРС

Это **флагманский открытый продукт агентства** (MIT): self-hosted Telegram-шлюз
к CLI-кодинг-агентам (Claude Code / Codex / Gemini). Мы развиваем его как
топовый опенсорс-ресурс: код, доки и лендинг — витрина инженерной культуры.

- GitHub (публичный): https://github.com/Creative-Agency-Here/HereAssistant
- Лендинг: https://hereassistant.hereagency.ru (исходник — `site/index.html`,
  деплой: `rsync -av site/ here@dell:/var/www/hereassistant.hereagency.ru/`)
- Любое изменение оценивай глазами внешнего контрибьютора: понятность,
  документированность, отсутствие внутренней «кухни» агентства в публичном коде.

---

# ДВОЙНАЯ ИНТЕГРАЦИЯ РЕПОЗИТОРИЕВ (ОБЯЗАТЕЛЬНО)

У репозитория ДВА remote, и они обязаны оставаться синхронными:

| Remote | Куда | Роль |
|---|---|---|
| `origin` | git.hereagency.ru (Gitea) | внутренний: вебхук шлёт уведомления в Telegram/CRM |
| `github` | github.com/Creative-Agency-Here (public) | публичное зеркало для сообщества |

**Правило: каждый пуш идёт в ОБА remote:**

```bash
git push && git push github master
```

Рассинхрон зеркал — баг, чини сразу. PR от внешних контрибьюторов приходят в
GitHub → после merge не забудь дотолкнуть в origin.

## Гигиена публичного репо (перед КАЖДЫМ пушем в github)

- Секреты/рантайм НЕ коммитятся: `.env`, `bridge.sqlite3`, `.runtime/`,
  auth-файлы провайдеров — всё в `.gitignore`, не ослабляй его.
- Внутренние ТЗ/аудиты (`docs/gpt-agent-audit.md`, `docs/claude-implementation-tz.md`)
  игнорируются — «кухню» в публичный репо не пушим.
- Быстрый секрет-скан при сомнении:
  `git log -p -2 | grep -nE "(TELEGRAM_BOT_TOKEN=[0-9]|sk-ant-|hvs\.|[0-9]{8,10}:[A-Za-z0-9_-]{30,})"`
- Приватные домены/адреса агентства не хардкодить в дефолтах кода (прецедент:
  `WEBAPP_URL` — дефолт сделан пустым).

---

# ГЛАВНЫЙ ПРИНЦИП ПРОДУКТА: PRIVACY-FIRST (НЕ ОСЛАБЛЯТЬ)

По умолчанию каждый проект `private`: содержимое сообщений/диффы не хранятся,
наружу (CRM/сервисный API) не уходит ничего. Единственная точка решений —
`core/project_config.py` (default deny; `.hereassistant/project.yml` — явный
opt-in per-project). **Любая новая фича, сохраняющая или отправляющая данные
проекта, обязана проходить через эти гейты.** Сервисный токен (`SERVICE_API_TOKEN`)
не обходит политику; пустой токен = сервисный API отключён (503), а не открыт.
Подробности: `docs/privacy.md`, требования к PR — `CONTRIBUTING.md`.

Прецедент-мотивация: партнёр ведёт 6–7 сторонних проектов через этого бота —
утечка их данных в CRM недопустима архитектурно, а не по договорённости.

---

# АРХИТЕКТУРНЫЕ ИНВАРИАНТЫ

- **Провайдеры — CLI-subprocess'ы** (подписки, не API-ключи), изоляция аккаунтов
  через `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `HOME` (`.runtime/cli_homes/`).
  Не заменять на API-шлюзы.
- `CLAUDE_PERMISSION_MODE=bypassPermissions` ЗАПРЕЩЁН (авто-одобрение Bash/Write
  = RCE через prompt-injection).
- **Rich Messages (Bot API 10.1)** — `utils/rich.py` (сырые вызовы API, aiogram
  их ещё не знает): финалы `sendRichMessage`, стрим `sendRichMessageDraft`.
  Классический HTML-путь — обязательный фолбэк, не удалять.
- Ubuntu — основной путь (PM2+nginx, `docs/ubuntu-pm2-nginx.md`), Windows — legacy.
- SQLite (`bridge.sqlite3`) — намеренно; PostgreSQL-миграцию не начинать без
  отдельного решения владельца.

---

# ПРОВЕРКИ ПЕРЕД КОММИТОМ

```bash
python3 -m compileall bot.py core handlers providers utils webapp/api
bash scripts/check_runtime.sh                      # sanity окружения
( cd webapp/front && npm run generate )            # если трогал фронт
git status --short                                 # никаких .env/.runtime/БД
```

---

# КАРТА ДОКУМЕНТАЦИИ

- `README.md` (EN) / `README.ru.md` — витрина и полное описание.
- `docs/ubuntu-pm2-nginx.md` — production-runbook.
- `docs/providers.md` — CLI-провайдеры и auth-homes.
- `docs/privacy.md` — режимы приватности и `project.yml`.
- `SECURITY.md` — модель угроз (это RCE-шлюз by design), `CONTRIBUTING.md` — правила PR.
- `ARCHITECTURE.md`, `TZ.md` — историческая внутренняя документация (легаси).
