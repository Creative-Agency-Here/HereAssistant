# Git-авторизация пользователей и мобильный WebApp

Status: design approved for implementation; production credentials are not changed.

English version: [git-auth-and-mobile-webapp.md](git-auth-and-mobile-webapp.md)

## Цель

Каждый Telegram-пользователь подключает собственный GitHub/Gitea аккаунт,
выбирает только разрешённые репозитории и работает с ними через собственный
Git broker. HereAssistant не просит пароль, не принимает токены сообщением боту
и не хранит Git credentials открытым текстом в SQLite или project workspace.

```text
Telegram identity
        ↓
HereAssistant user_id
        ↓
Git connection metadata ────── selected repositories
        ↓                              ↓
per-user Git broker UID ─────── authorized project roots
        ↓
credential vault (token never reaches the coding-agent UID)
```

## Почему одного `ha-user` недостаточно

Provider runner запускает coding agent, а тот умеет выполнять shell-команды.
Если положить PAT/OAuth token в его `HOME`, `.git-credentials`, переменную
окружения или SQLite, prompt injection либо ошибочная команда сможет прочитать
секрет. Поэтому Git credential нельзя добавлять в уже созданный `ha-ilya` или
будущий `ha-pavel`.

Для production нужны две Unix identity на человека:

```text
ha-ilya       — Claude/Codex/Gemini, без Git credentials
ha-ilya-git   — только валидированные clone/fetch/pull/push

ha-pavel      — Claude/Codex/Gemini, без Git credentials
ha-pavel-git  — только валидированные clone/fetch/pull/push
```

Git broker проверяет Telegram `user_id`, host, remote URL, project root и точную
операцию. Он никогда не возвращает token вызывающему процессу. Кодовый runner и
Git broker используют отдельные UID и общую project-группу с setgid/umask, чтобы
оба могли обновлять worktree, но не могли читать HOME друг друга.

## Рекомендуемый пользовательский flow

### WebApp — основной интерфейс

В разделе `Настройки → Git-аккаунты` пользователь видит карточки:

- Git host и provider (`GitHub`, `Gitea`, позднее `GitLab`);
- подключённый login/avatar;
- состояние: подключён, истекает, отозван, нужна повторная авторизация;
- выданные scopes без показа token;
- число разрешённых репозиториев и время последнего Git-действия;
- действия `Подключить`, `Репозитории`, `Переподключить`, `Отключить`.

Подключение:

1. Пользователь нажимает `Подключить Git` и выбирает host.
2. API создаёт одноразовую OAuth session, привязанную к Telegram `user_id`,
   случайному `state`, PKCE verifier и сроку не более 10 минут.
3. WebApp открывает официальный экран Git provider через Telegram `openLink`.
4. После consent provider возвращает пользователя на HTTPS callback HereAssistant.
5. Backend проверяет `state`, Telegram owner и PKCE, обменивает code на token и
   сразу передаёт секрет в vault Git broker-а через локальный защищённый канал.
6. SQLite получает только connection metadata и opaque `vault_ref`.
7. Пользователь выбирает репозитории, которые разрешено импортировать.
8. `Клонировать` создаёт private project текущего пользователя; чужой connection
   или repository ID не принимается даже при подмене HTTP payload.

### Telegram — вход и уведомления

Команда `/git` показывает только статус и кнопки:

- `Открыть Git-настройки` — WebApp;
- `Мои репозитории` — WebApp с нужным экраном;
- `Переподключить` — запускает тот же OAuth flow;
- `Отозвать` — требует inline-confirmation.

Бот не должен принимать PAT, пароль или private SSH key текстовым сообщением.
Он уведомляет об успешном подключении, истечении/revoke и результате push.

### CLI — резервный headless flow

`manage.py git connect` используется оператором либо пользователем через свой
ограниченный runner. Для GitHub допустим Device Flow; для Gitea CLI печатает URL
и открывает Authorization Code + PKCE flow. Ручной PAT — только fallback через
masked stdin/TTY, без аргумента командной строки, shell history и логирования.
CLI вызывает тот же backend/service layer и тот же vault, что WebApp.

## Provider strategy

### Gitea

Основной вариант — OAuth2 Authorization Code + PKCE. Gitea поддерживает PKCE и,
начиная с v1.23, granular scopes. Запрашивать минимум `read:user` и
`write:repository`; неизвестные scopes запрещены, потому что некоторые версии
Gitea могут откатиться к полному доступу. Host обязан быть заранее разрешён
оператором, а OAuth endpoints берутся из OIDC discovery либо server config, не из
произвольного URL пользователя.

### GitHub

Для polished production предпочтителен GitHub App: оператор задаёт app один раз,
а пользователь выбирает конкретные repositories при installation. Installation
tokens короткоживущие и ограничены выбранными repos/permissions.

Для простого self-hosted setup возможны два fallback:

- fine-grained PAT, вставленный один раз в HTTPS WebApp и немедленно отправленный
  в vault; пользователь сам ограничивает repositories и срок действия;
- OAuth Device Flow для headless CLI с явным предупреждением, что OAuth `repo`
  scope шире repository selection GitHub App.

Token, встроенный в user-info часть remote URL, всегда запрещён.

Официальные источники:

- [GitHub Apps vs OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)
- [GitHub OAuth and Device Flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
- [Gitea OAuth2 Provider](https://docs.gitea.com/development/oauth2-provider)
- [Git credential helpers](https://git-scm.com/docs/gitcredentials)

## Модель данных

Секретных колонок нет:

```text
git_connections
- id
- user_id
- provider
- host
- external_user_id
- external_login
- avatar_url
- vault_ref
- scopes_json
- status
- expires_at
- created_at
- updated_at
- last_used_at

git_repository_grants
- id
- connection_id
- external_repository_id
- owner_name
- repository_name
- clone_url
- default_branch
- permission
- enabled
- created_at
- updated_at

git_auth_sessions
- id
- user_id
- provider
- host
- state_hash
- pkce_verifier_encrypted_or_ephemeral
- status
- expires_at
- created_at
```

`git_auth_sessions` удаляются после callback/expiry. Если verifier приходится
сохранять между API-процессами, он идёт в короткоживущий encrypted store, не в
обычное поле SQLite. Audit events хранят только факт `connected/revoked/push`,
connection/project/remote и результат без URL credentials, code или token.

## API contract

Все endpoints требуют валидный Telegram Mini App `initData`; `user_id` берётся
только из middleware, никогда из request body.

```text
GET    /api/git/connections
POST   /api/git/connections/start
GET    /api/git/oauth/callback/{provider}
POST   /api/git/connections/{id}/refresh
DELETE /api/git/connections/{id}

GET    /api/git/connections/{id}/repositories
POST   /api/git/repositories/{id}/grant
DELETE /api/git/repositories/{id}/grant
POST   /api/git/repositories/{id}/clone

GET    /api/projects
GET    /api/projects/{id}/git
POST   /api/projects/{id}/git/pull
POST   /api/projects/{id}/git/push/prepare
POST   /api/projects/{id}/git/push/confirm
```

Push использует короткоживущий confirmation nonce, связанный с `user_id`,
`project_id`, commit SHA и набором remotes. Перед реальным push broker выполняет
dry-run всех targets; если любой preflight падает, ни один реальный push не
начинается. Это не делает два remote атомарными, но существенно уменьшает шанс
частичного рассинхрона.

## Мобильный WebApp по мотивам Claude

Из предоставленных экранов берём паттерны, но сохраняем собственный бренд:

- главный экран — список sessions, сгруппированный по project, с online/running
  индикатором, последним запросом и быстрым `Новая задача`;
- detail — компактная sticky header с названием session и project;
- tool calls в ленте свёрнуты в строки `Read`, `Edit`, `Bash`, `Workflow`;
- tap открывает bottom sheet с command/output или diff;
- file activity показывает зелёные/красные `+N/-N` и список затронутых файлов;
- background tasks — отдельный bottom sheet/экран с running/finished, phases,
  agents, tokens и elapsed time;
- composer фиксирован снизу, учитывает safe-area и не перекрывает контент;
- destructive actions требуют отдельного confirm sheet;
- крупные touch targets, минимум декоративного шума, тёмная тема Telegram.

Не переносим сейчас фальшивые элементы: WebApp не показывает composer, stop,
parallel workflows или live tool details, пока backend действительно не умеет
безопасно выполнить соответствующее действие.

### Новая навигация

```text
Сессии     — текущие и последние диалоги
Проекты    — repos, branches/worktrees, Git status/actions
Активность — tool calls, background tasks, file changes
Экономия   — RTK tokens/gain
Настройки  — agent accounts, Git accounts, security
```

На телефоне — четыре основных tab и `Настройки` через header/menu. На desktop —
sidebar. Existing `/history`, `/edits`, `/stats` сохраняются как redirects либо
вложенные views, чтобы не ломать старые ссылки.

## Этапы реализации

### P0. Контракты и защита текущего push

- [x] Добавить dry-run preflight всех remote перед реальным push.
- [x] Разделить ошибки `AUTH_REQUIRED`, `REMOTE_DENIED`, `PREFLIGHT_FAILED`.
- [x] Добавить tests на отсутствие partial push после неуспешного preflight.
- [x] Зафиксировать typed DTO и migration tests для metadata tables без secrets.

### P1. Отдельный Git broker и vault

- [x] Добавить отдельный Git UID/config mapping на пользователя.
- [x] Вынести Git subprocess из code runner в dedicated broker boundary.
- [x] Реализовать portable Git credential-helper proxy interface.
- [x] Реализовать root/systemd-backed vault socket service с `SO_PEERCRED` и
  `LoadCredentialEncrypted`.
- [x] Реализовать owner-bound atomic rotation encrypted bundle: credential идёт
  только через bounded stdin, plaintext-файл не создаётся, reload контролируемый.
- [x] Связать Gitea public-client PKCE callback с безопасной rotation encrypted
  bundle и reload service.
- [ ] Проверить, что code runner не читает vault/HOME и не получает token через
  argv, env, stdout, logs, process list или Git remote.
- [x] Запретить expired OAuth credentials независимо в metadata и vault query.
- [x] Ротировать Gitea refresh/access tokens внутри root vault, возвращая core
  только несекретный `expires_at`.
- [x] Автоматически обновлять истекающий Gitea credential перед разрешённой
  Git-операцией; повторный OAuth нужен только при revoke/нерабочем refresh token.
- [x] Добавить read-only host canary и cross-user negative tests.
- [ ] Запустить live credential canary перед production activation.

### P2. Gitea OAuth + WebApp settings

- [x] Добавить owner-filtered connection migrations/repository/service/API.
- [x] Реализовать Authorization Code + PKCE и exact-host allowlist.
- [x] Добавить экран подключения/status/revoke Git accounts.
- [x] Добавить каталог репозиториев и explicit grant picker.
- [x] Добавить `/git` как безопасную точку входа без credentials в чате.
- [x] Проверять hardened HTTPS clone/pull/push через repository grant + broker.

### P3. GitHub connection

- [ ] Реализовать GitHub App installation flow как основной вариант.
- [ ] Добавить fine-grained PAT one-time fallback для self-hosted instances.
- [ ] Добавить Device Flow в headless CLI с scope warning.
- [ ] Проверить org approval, token expiry/refresh и revoke.

### P4. Claude-inspired mobile workspace

- [ ] Перестроить shell/navigation без изменения API semantics.
- [ ] Добавить sessions/projects cards и реальные status indicators.
- [ ] Добавить bottom sheets для существующих file changes/tool summaries.
- [ ] Добавить background task/workflow UI только после появления typed backend
  events; до этого не имитировать несуществующую функциональность.
- [ ] Провести iPhone/Android safe-area, keyboard, WebView и deep-link probes.

## Acceptance criteria

- Пользователь подключает только свой Git account и видит только его metadata.
- Token никогда не попадает в Telegram message, SQLite, remote URL, project tree,
  agent HOME, application log, command argv или RTK history.
- Code runner не может прочитать либо запросить raw credential у Git broker.
- Connection и repository grant нельзя переиспользовать с чужим `user_id`.
- Clone/pull/push разрешены только для granted repository и authorized project.
- Revoke немедленно блокирует новые Git operations и помечает connection disabled.
- Два пользователя могут независимо работать с одноимёнными repositories.
- WebApp показывает только реально доступные действия и корректно работает как
  Telegram Mini App, обычный mobile browser и desktop browser.

## Что не делать

- Не копировать существующую авторизацию владельца в профиль нового пользователя.
- Не использовать общий machine token для private user repositories.
- Не принимать Git password/PAT через чат Telegram.
- Не хранить secrets в `accounts`, `projects`, `git_connections` или `.env` WebApp.
- Не использовать `git credential-store` в HOME coding agent.
- Не разрешать произвольный self-hosted OAuth host без server-side allowlist.
- Не считать `chmod 600` защитой, если credential и agent работают под одним UID.
