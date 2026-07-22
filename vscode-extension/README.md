# HereAssistant for VS Code

Локальный workbench для HereAssistant — статус-бар, сессии, Git, деплой и запуск AI-терминала прямо из VS Code.

## Установка

### Из Open VSX (Cursor, Windsurf, VSCodium)
Поиск расширений → `HereAssistant` → Install

### Из VSIX (любой IDE)
```bash
# Скачать последний релиз
curl -L -o /tmp/ha.vsix https://github.com/Creative-Agency-Here/HereAssistant/releases/latest/download/hereassistant-vscode-0.8.1.vsix

# Установить
code --install-extension /tmp/ha.vsix        # VS Code
cursor --install-extension /tmp/ha.vsix      # Cursor
```

### Из исходников
```bash
git clone https://github.com/Creative-Agency-Here/HereAssistant.git
cd HereAssistant
python3 scripts/package_vscode_extension.py
code --install-extension dist/hereassistant-vscode-0.8.1.vsix
```

## Что появляется в VS Code

### Статус-бар (внизу слева)

```
$(sparkle) Here
```

Клик → **Quick Actions** меню:

| Действие | Что делает |
|---|---|
| 🎯 Запустить задачу по промпту | Спросит текст → откроет терминал с `ha` → отправит задачу |
| 📋 Все сессии | Список сессий за неделю: статус, возраст, превью. Клик → переход |
| ➕ Новый пустой чат | Открывает терминал с `ha` без промпта |
| 🌐 Открыть HereCRM | Открывает CRM в браузере |
| 👤 Управление AI-аккаунтами | Запускает `manage.py` в терминале |
| ⚙️ Настроить подключение | Мастер первичной настройки |

### Source Control сайдбар

Вкладка **HereAssistant · Git и деплой** рядом с обычным Git:

- Текущая ветка + dirty/ahead/behind счётчики
- Кнопки Pull / Push
- Статус деплоя (deployed / partial / pending)

### HereAssistant · Сессии

Вкладка в SCM-сайдбаре со слепком AI-сессий:

```
⚙ рефакторинг auth модуля          ⚙ работает · 2 мин назад · терминал открыт
✓ анализ конкурентов               ✓ открыта · AFK · 15 мин назад
○ генерация отчёта                 ○ закрыта · 2 ч назад
✗ деплой на прод                   ✗ ошибка · 30 мин назад
```

Статусы:
- **⚙ работает** — агент выполняет задачу
- **✓ открыта · AFK** — терминал открыт, агент простаивает
- **○ закрыта** — терминал закрыт или протух (>30 мин без обновлений)
- **✗ ошибка** — задача упала

Клик по сессии → переход к живому терминалу или возобновление прошлой.

## Терминал (Ink TUI)

Расширение запускает `ha` — полноценный TUI-чат на Ink (React for CLI):

```
┌─ HA · local-qwen · qwen3.8-max · edits✓ · Shift+Tab · MyProject · 3 задач ─
│ ↑ #2 проанализируй auth · скролл                                          │
│ #1 › привет                                                                │
│   ✓ 📄 read_file src/auth.ts                    [3 строки — клик раскрыть] │
│   ✓ ⚡ rtk grep -r "login" src/                 [12 строк — клик раскрыть] │
│                                                                            │
│   Нашёл проблему в validateToken...                                        │
│   ── 4.2s · 8.3k tok ──                                                    │
├────────────────────────────────────────────────────────────────────────────┤
│ › сообщение… (пробел — голос, Ctrl+V — фото, ! shell)                     │
└────────────────────────────────────────────────────────────────────────────┘
```

### Горячие клавиши в TUI

| Клавиша | Действие |
|---|---|
| `Enter` | Отправить |
| `Alt+Enter` | Новая строка |
| `↑↓` | История команд |
| `Tab` | Автодополнение /команд и @файлов |
| `Shift+Tab` | Cycle permission mode (edits✓ → auto → read-only → ask) |
| `Пробел` | Toggle голосовой записи (real-time, macOS native) |
| `Ctrl+V` | Вставить фото из clipboard |
| `Ctrl+G` | Открыть в $EDITOR |
| `!команда` | Выполнить shell-команду |
| `PgUp/PgDn` | Скролл ±10 строк |
| `Клик по tool-блоку` | Раскрыть/свернуть вывод |
| `Колёсико` | Скролл |
| `Ctrl+C` | Выход |

### 21 команда

```
/help /model /account /status /resume /rename /fork /search /bg
/theme /archive /delete /voice /mcp /plain /image /diff /new /compact /exit
```

## Настройка

При первом запуске расширение спросит:

1. **Папку HereAssistant** — где лежит `cli/dist/index.js`
2. **Web API URL** (опционально) — для CRM-счётчиков и heartbeats
3. **Имя контура** — например `Ilya's MacBook` или `DE server`
4. **Ключ доступа** — хранится в VS Code SecretStorage (не в settings.json)

Без API расширение работает полностью локально: терминал, статус-бар, Git, сессии.

### Settings

| Параметр | По умолчанию | Описание |
|---|---|---|
| `hereAssistant.installationPath` | — | Путь к HereAssistant |
| `hereAssistant.terminalLocation` | `editor` | Где открывать терминал: `editor` (таб) или `panel` (внизу) |
| `hereAssistant.mouseSupport` | `true` | Мышь в TUI. `false` → выделение без Shift |
| `hereAssistant.pollIntervalSeconds` | `3` | Частота обновления сессий |
| `hereAssistant.deployCommand` | — | Команда деплоя (с подтверждением) |

## 4 провайдера

Расширение работает с любым из четырёх AI-провайдеров:

| Провайдер | Аккаунт | Модель по умолчанию |
|---|---|---|
| Qwen Code | `local-qwen` | qwen3.8-max-preview |
| Claude Code | `local-claude` | claude-opus-4-7 |
| Codex | `local-codex` | gpt-5 |
| Gemini | `local-gemini` | gemini-2.5-pro |

Переключение: `/account` в TUI или выбор при запуске.

## Development

```bash
# Открыть Extension Development Host
code --extensionDevelopmentPath="$PWD/vscode-extension" "$PWD"

# Собрать VSIX
python3 scripts/package_vscode_extension.py

# Установить локально
code --install-extension dist/hereassistant-vscode-0.8.1.vsix
```

## Лицензия

MIT