# План: вебап ассистента в стиле админ-панели

## Суть (TL;DR)
Сейчас вебап — минимальный Nuxt-SPA на Tailwind с самописными карточками. Админка — «взрослый» SPA: **Vite + Vue 3.5 + Naive UI + Pinia**, тёмная «стеклянная» тема, фиолетовый акцент, готовая библиотека компонентов и монорепо с общим конфигом.

Чтобы вебап выглядел и ощущался **как админка** — пересобрать его на том же стеке (Vite + Vue + Naive UI), переиспользовать тему и компоненты из монорепо, но **оставить лёгким** (это Telegram Mini App — грузится по сети, без тяжёлых echarts/xterm/tanstack).

---

## 1. Что есть сейчас (наш вебап)
- **Стек:** Nuxt 3.17 SSG (ssr:false), Tailwind, markdown-it.
- **Тема:** самописная тёмная — фон `#0e1116`, синий акцент `#7aa2ff`, шрифты Inter/JetBrains.
- **Страницы:** Сейчас / История / Правки — всё инлайн в pages, без компонент-библиотеки.
- **Telegram:** initData-авторизация (работает), SSG-статика через nginx, WS-лог.

## 2. Что есть в админке (эталон стиля)
- **Стек:** Vite 7 + Vue 3.5 + **Naive UI 2.44** + Pinia + vue-router, TypeScript.
- **Тема:** тёмная «glass» — фон `#050505`, поверхности `#181818`, `backdrop-blur(40px)`, акцент `#AB60F6` (6 пресетов + кастом), шрифты **Core Sans C** / **Bebas Neue Pro**, радиусы 12–20px.
- **Тема через** `NConfigProvider` + `themeOverrides` (Card 16px, Modal 20px, Button bold).
- **Архитектура FSD:** `app → pages → widgets → features → entities → shared`.
- **Общие обёртки:** `AppCard`, `AppDataTable` (на NDataTable + TanStack), `AppFormInput`, `AppModal`, `SmartAvatar`, ячейки таблиц.
- **Naive UI в ходу:** NCard, NDataTable, NTabs, NTag, NButton, NModal, NDrawer, NMenu, NPopover, NSelect, NForm.
- **Монорепо:** `Sites/HereAgency/apps/admin-panel`, общий `@here/config` (Tailwind base).

## 3. Разница и решение

| | Наш вебап | Админка |
|---|---|---|
| Фреймворк | Nuxt 3 (SSG) | Vite + Vue 3 (SPA) |
| UI-кит | нет (самопис) | **Naive UI** |
| Тема | синий, плоский | фиолетовый, glass |
| Компоненты | инлайн | библиотека обёрток |
| State | ref | Pinia |
| Шрифты | Inter | Core Sans / Bebas |

**Решение:** пересобрать вебап на стеке админки и переиспользовать её тему/обёртки. Тяжёлое (echarts, xterm, tanstack-table, socket.io-клиент CRM) **не тянуть** — Mini App должен быть лёгким.

**Где разместить (2 варианта):**
- **A) Внутри монорепо** — `Sites/HereAgency/apps/assistant`. Переиспользует `@here/config`, общие компоненты, единую тему → максимальная консистентность с админкой. Минус: на DE-1 надо ставить зависимости всего монорепо.
- **B) Отдельный Vite-проект** — `HereAssistant/webapp/front` переписываем с нуля на Vite+Vue+Naive UI, тему копируем из админки. Изолированно, проще на сервере, но дублирование темы/компонентов.

→ **Рекомендую A**, если монорепо реально собирать на DE-1. Иначе **B**.

## 4. Целевой дизайн (как у админки)
- Тема через `NConfigProvider` + `themeOverrides`: тёмная, акцент `#AB60F6`, радиусы, glass-поповеры/модалки.
- Шрифты Core Sans / Bebas — **если есть файлы/лицензия**; иначе Inter как fallback (визуально близко).
- **Layout:** `NLayout` (sider + header + content) вместо самописного сайдбара; `NMenu` для навигации. На мобиле — компактный Mini-App-режим.
- Компоненты: `AppCard`, `NDataTable` (история, правки), `NTabs`, `NTag` (статусы), `NButton`, `NDrawer`/`NModal` (детали диалога).

## 5. Страницы → компоненты Naive UI
- **Сейчас:** `NCard` со статусом (`NTag` online/idle + пульс), список действий (`NTimeline`/список), лог (`NScrollbar` + `<pre>`), кнопка «Прервать» (`NButton` danger).
- **История:** `NDataTable` (колонки #, модель, аккаунт, сообщений, дата) — сортировка/поиск (`NInput`), клик → `NDrawer` или роут с диалогом.
- **Диалог:** сообщения карточками, markdown через markdown-it (как сейчас); markdown-таблицы → `.md` или `NDataTable`.
- **Правки:** группировка по файлу (`NCollapse`), дифф (самописный с номерами строк — уже есть), фильтры (`NInput`/`NSelect`).
- **Статистика:** лёгкие графики **без echarts** (мелкий svg/`NProgress`) — токены/модели/дни.
- **Настройки:** `NForm` — модель (`NSelect`), аккаунт, тема.

## 6. Telegram Mini App — ограничения
- Остаётся **статика** (Vite build → `dist`, отдаём nginx) — Mini App не нужен SSR.
- **initData-авторизация** (уже работает) — порт `useApi`/WS переносим 1:1.
- `telegram-web-app.js` в `index.html`, плагин `ready()`/`expand()`.
- Опц.: синхронизация с `Telegram.WebApp.themeParams` (подстройка под тему Telegram).
- **Лёгкость:** Naive UI tree-shaking (импортировать только нужные компоненты), без тяжёлых либ → бандл небольшой.

## 7. Переиспользование из монорепо
- `@here/config` Tailwind base + `themeOverrides` из `admin-panel/src/App.vue`.
- Обёртки `AppCard`, `AppDataTable`, `AppFormInput`, glass-стили, шрифты.
- **API-слой свой** (assistant API на `:8200`, не CRM): копируем паттерн `baseClient` (ofetch) + initData-заголовок.

## 8. Этапы
1. **Каркас:** Vite+Vue+Naive UI+router+Pinia, `NConfigProvider` с темой админки, layout (sider+header). Telegram SDK + initData-auth + сборка в `dist`.
2. **«Сейчас»** (статус + лог) на Naive UI.
3. **История + диалог** (`NDataTable` + `NDrawer` + markdown).
4. **Правки** (группировка + дифф — переносим готовую логику).
5. **Статистика + Настройки** (новые).
6. Сборка → деплой через nginx (как сейчас) → проверка в Telegram.

## 9. Риски / нюансы
- **Шрифты** Core Sans/Bebas — нужны файлы/лицензия; иначе Inter fallback.
- **Bundle** — следить, чтобы Naive UI с tree-shaking не разросся (Mini App грузится по сети).
- **Монорепо-сборка на DE-1** (вариант A) — нужен Node + установка зависимостей всего монорепо.
- **Авторизация/WS** — переносятся почти без изменений.

## 10. Рекомендация и оценка
- Хочешь максимально «как админка» и готов собирать монорепо на сервере → **вариант A** (`apps/assistant`, общие тема+компоненты).
- Хочешь быстрее и изолированно → **вариант B** (отдельный Vite-проект, тему копируем).
- В обоих: **Naive UI + тёмная glass-тема + акцент #AB60F6 + AppCard/NDataTable/NTabs** → вебап станет визуально и по UX как админка.

**Оценка по времени:** каркас+тема ~0.5 дня · перенос 3 страниц ~1–1.5 дня · статистика+настройки ~0.5–1 день. **Итого ~2–3 дня** работы.
