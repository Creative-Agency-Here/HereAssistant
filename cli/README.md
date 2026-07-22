# HereAssistant CLI (`ha`)

**Unified AI terminal** — 4 провайдера, 21 команда, голос, MCP, fullscreen TUI.

Один CLI-инструмент для Claude Code, Codex, Gemini CLI и Qwen Code с UX на уровне Claude Code и уникальными фичами, которых нет ни у одного аналога.

```
┌─ HA · local-qwen · qwen3.8-max · edits✓ · Shift+Tab · Site-HereAgency · 3 задач · 12.5k tok ─
│ #2 › проанализируй модуль авторизации                                            │
│   ✓ 📄 read_file src/auth.ts                         [3 строки — клик раскрыть]  │
│   ✓ ⚡ rtk grep -r "login" src/                      [12 строк — клик раскрыть]  │
│   ⏳ ✏️ edit src/auth.py                                                         │
│                                                                                  │
│   Нашёл проблему в validateToken — отсутствует проверка expiration claim...      │
│   ── 4.2s · 8.3k tok ──                                                          │
├──────────────────────────────────────────────────────────────────────────────────┤
│ › сообщение… (пробел — голос, Ctrl+V — фото, ! shell)                           │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Установка

```bash
cd HereAssistant/cli
npm install
npm run build
# Swift binary для real-time голоса (macOS):
swiftc -o bin/voice_rt src/voice_rt.swift -framework Speech -framework AVFoundation
```

## Запуск

```bash
ha                        # выбор аккаунта стрелками
ha -a local-qwen          # сразу Qwen Code
ha -a local-codex         # сразу Codex
ha -p fast                # профиль из .hereassistant/config.json
ha --resume <session-id>  # продолжить сессию
```

## Сравнение с аналогами

| Фича | Claude Code | Codex | Gemini | Qwen | **HA CLI** |
|---|:---:|:---:|:---:|:---:|:---:|
| **Провайдеры** | 1 | 1 | 1 | 1 | **4** |
| **Переключение провайдеров** | ❌ | ❌ | ❌ | ❌ | **✅ /account** |
| Многострочный ввод | ✅ | ✅ | ✅ | ✅ | ✅ |
| История ↑↓ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Автодополнение /команд | ✅ | ✅ | ✅ | ✅ | ✅ |
| @file автодополнение | ✅ | ✅ | — | — | ✅ |
| Shell mode `!` | ✅ | — | — | — | ✅ |
| Внешний редактор Ctrl+G | ✅ | — | — | — | ✅ |
| **Голос real-time** | ✅ | — | — | — | **✅ пробел** |
| **Голос batch (mlx-whisper)** | — | — | — | — | **✅ /voice** |
| Paste фото Ctrl+V | ✅ | — | ✅ | — | ✅ |
| Стриминг + Markdown | ✅ | ✅ | ✅ | ✅ | ✅ |
| Подсветка синтаксиса | ✅ | ✅ | ✅ | ✅ | ✅ |
| Thinking display | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Кликабельные tool-блоки** | ✅ | ✅ | ✅ | ✅ | **✅ mouse** |
| **Inline-картинки** | ✅ | — | — | — | **✅ iTerm2+unicode** |
| **Анимация голоса** | ✅ | — | — | — | **✅ пульс+волна** |
| Resume / picker | ✅ | ✅ | — | — | ✅ |
| Rename / fork | ✅ | ✅ | — | — | ✅ |
| Archive / delete | — | ✅ | — | — | ✅ |
| Background агент | ✅ | — | — | — | ✅ |
| Память (CLAUDE.md) | ✅ | ✅ | — | ✅ | **✅ все форматы** |
| MCP | ✅ | ✅ | ✅ | ✅ | **✅ unified** |
| Темы | ✅ | — | — | — | **✅ 4 темы** |
| **Permission mode Shift+Tab** | ✅ | ✅ | ✅ | ✅ | **✅ 4 режима** |
| Config profiles | — | ✅ | — | — | **✅ -p name** |
| Fullscreen TUI | ✅ | ✅ | — | — | ✅ |
| Terminal title OSC | ✅ | ✅ | — | — | ✅ |
| **Scroll-хедер (как Telegram)** | — | — | — | — | **✅** |
| **Нумерация сообщений** | — | — | — | — | **✅ #1 #2** |
| **Framed media (отчёты)** | — | — | — | — | **✅** |
| **Diff review с рамкой** | ✅ | ✅ | — | — | **✅ per-file** |

### Покрытие vs Claude Code: **37/38 (97%)**

Единственное не покрытое: Vim mode (Ctrl+G покрывает 90% кейсов).

## Уникальные фичи HA

| Фича | Описание |
|---|---|
| **4 провайдера** | Claude + Codex + Gemini + Qwen в одном TUI |
| **Unified MCP** | Читает `.qwen/settings.json` + `.claude/settings.json` + `.mcp.json` |
| **Unified память** | CLAUDE.md + AGENTS.md + QWEN.md — автозагрузка |
| **Голос dual-mode** | Real-time (SFSpeechRecognizer, on-device) + batch (mlx-whisper, M2 GPU) |
| **Inline-картинки** | iTerm2 protocol + unicode half-block fallback |
| **Scroll-хедер** | Плавающий контекст при скролле (как дата в Telegram) |
| **Framed media** | Красивые рамки для image/video/report |
| **Config profiles** | `ha -p fast` / `ha -p deep` / `ha -p codex` |

## Горячие клавиши

| Клавиша | Действие |
|---|---|
| `Enter` | Отправить |
| `Alt+Enter` | Новая строка |
| `↑↓` | История / навигация |
| `Tab` | Автодополнение /команд и @файлов |
| `Shift+Tab` | Cycle permission mode |
| `Пробел` | Toggle голосовой записи |
| `Ctrl+V` | Вставить фото из clipboard |
| `Ctrl+G` | Внешний редактор ($EDITOR) |
| `!команда` | Shell-команда |
| `PgUp/PgDn` | Скролл ±10 строк |
| `Клик по tool-блоку` | Раскрыть/свернуть |
| `Колёсико` | Скролл |
| `Ctrl+C` | Выход |

## Команды (21)

```
/help /model /account /status /resume /rename /fork /search /bg
/theme /archive /delete /voice /mcp /plain /image /diff /new /compact /exit
```

## Config profiles

```json
// .hereassistant/config.json
{
  "defaultModel": "qwen3.8-max-preview",
  "theme": "dark",
  "profiles": {
    "fast":  { "defaultModel": "qwen3.6-flash", "theme": "mono" },
    "deep":  { "defaultModel": "qwen3.8-max-preview" },
    "codex": { "defaultProvider": "codex", "defaultModel": "gpt-5" }
  }
}
```

## Permission modes

| Режим | Файлы | Shell | Когда |
|---|---|---|---|
| `edits✓` | авто | спрашивает | обычная разработка |
| `auto` | AI решает | AI решает | доверенный проект |
| `read-only` | ❌ | ❌ | планирование без риска |
| `ask` | спрашивает | спрашивает | осторожный режим |

Переключение: **Shift+Tab**

## Технологии

- **Ink** (React for CLI) + TypeScript
- **better-sqlite3** — аккаунты из bridge.sqlite3
- **Swift** — real-time voice (SFSpeechRecognizer, macOS native)
- **mlx-whisper** — batch voice (Apple Silicon GPU, 19x realtime)
- **ffmpeg** — audio recording (avfoundation)

## Структура

```
cli/src/
├── index.tsx              — точка входа (-a, -p, --resume)
├── types.ts               — типы
├── db.ts                  — аккаунты из SQLite
├── commands.ts            — 21 slash-команда
├── config.ts              — .hereassistant/config.json + profiles
├── clipboard.ts           — paste image (osascript)
├── editor.ts              — Ctrl+G внешний редактор
├── memory.ts              — CLAUDE.md / AGENTS.md / QWEN.md
├── mcp.ts                 — unified MCP config
├── sessions.ts            — session picker (Claude/Qwen/Codex)
├── themes.ts              — 4 темы (dark/light/mono/neon)
├── terminal-title.ts      — OSC + spinner
├── terminal-images.ts     — iTerm2 + unicode half-block
├── voice.ts               — batch (mlx-whisper) + real-time (Swift)
├── voice_rt.swift         — SFSpeechRecognizer (macOS native)
├── hooks/
│   ├── useFullscreen.ts   — mouse reporting
│   └── useMouse.ts        — SGR mouse events via Ink useStdin
├── parsers/
│   └── stream.ts          — Claude/Qwen + Gemini stream-json
├── providers/
│   ├── index.ts           — фабрика makeProvider()
│   ├── claude.ts          — Claude Code
│   ├── codex.ts           — Codex CLI
│   ├── gemini.ts          — Gemini CLI
│   └── qwen.ts            — Qwen Code
└── components/
    ├── App.tsx            — выбор аккаунта
    ├── FullscreenChat.tsx — основной чат (fullscreen TUI)
    ├── ChatInput.tsx      — ввод: multiline, история, голос, @file
    ├── StatusBar.tsx      — аккаунт · модель · perm · токены
    ├── RunSummary.tsx     — время · токены
    ├── ToolCallBlock.tsx  — tool-вызовы (кликабельные)
    ├── FramedMedia.tsx    — рамки для image/video/report
    └── markdown.ts        — markdown + подсветка синтаксиса
```

## Лицензия

MIT — часть [HereAssistant](https://github.com/Creative-Agency-Here/HereAssistant).