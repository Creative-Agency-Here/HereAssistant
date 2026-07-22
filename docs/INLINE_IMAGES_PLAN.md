# Inline-изображения в HA CLI TUI

## Текущее состояние

Ctrl+V / `/image` сохраняет фото из clipboard во временный файл и передаёт путь провайдеру.
В чате показывается `📎 clipboard-xxx.png` — текстовая ссылка, без визуализации.

## Как работает у аналогов

### Claude Code
- **iTerm2**: proprietary escape-последовательность `\033]1337;File=...:base64\007`
- **Kitty**: graphics protocol `\033_G...;\033\\`
- **WezTerm**: поддерживает оба протокола выше
- **Fallback**: текстовый chip `[Image #N]` + путь к файлу
- Определение терминала: переменные окружения `TERM_PROGRAM`, `KITTY_WINDOW_ID`, `WEZTERM_UNIX_SOCKET`
- Ресайз: запрос размеров ячейки через `\033[14t` / `\033[16t`

### Codex CLI
- Нет inline-изображений в TUI
- Фото передаются через `-i <path>` как аргумент CLI

### Gemini CLI
- Нет inline-изображений в TUI
- Фото передаются через промпт

## Протоколы inline-изображений

### 1. iTerm2 Inline Images Protocol
```
\033]1337;File=name=<base64name>;size=<bytes>;width=<W>;height=<H>;inline=1:<base64data>\007
```
- Поддержка: iTerm2, WezTerm, Ghostty, foot
- Форматы: PNG, JPEG, GIF
- Размеры: в символах терминала (width=80 = 80 колонок)
- Автодетект: `TERM_PROGRAM=iTerm.app` или `LC_TERMINAL=iTerm2`

### 2. Kitty Graphics Protocol
```
\033_Ga=T,f=100,s=<W>,v=<H>;<base64chunk>\033\\
```
- Поддержка: Kitty, WezTerm, Ghostty, Konsole
- Форматы: PNG, JPEG, GIF, RGBA raw
- Передача чанками по 4096 байт
- Автодетект: `KITTY_WINDOW_ID` или `TERM=xterm-kitty`

### 3. Sixel Graphics
```
\033Pq ... sixel data ... \033\\
```
- Поддержка: xterm (с флагом), mlterm, foot, WezTerm, Black Screen
- Формат: растровый, пиксель за пикселем
- Требует конвертации изображения в sixel-формат
- Автодетект: `DA1` response содержит `;4;` (sixel support)

### 4. Unicode Half-Block (fallback для всех терминалов)
- Рендеринг изображения через символы `▀▄█░▒▓` с ANSI цветами
- Разрешение: 2 пикселя на символ (верх/низ через fg/bg цвет)
- Работает в ЛЮБОМ терминале с truecolor (24-bit)
- Качество низкое, но лучше чем ничего

## План реализации

### Фаза 1: Детект терминала (1 файл)

**`cli/src/terminal-images.ts`**

```typescript
type ImageProtocol = 'iterm2' | 'kitty' | 'sixel' | 'unicode' | 'none';

function detectProtocol(): ImageProtocol
function getImageDimensions(filePath: string): { width: number; height: number }
function getTerminalCellSize(): { cols: number; rows: number; pixelW: number; pixelH: number }
```

Детект по env-переменным:
| Переменная | Протокол |
|---|---|
| `TERM_PROGRAM=iTerm.app` | iterm2 |
| `LC_TERMINAL=iTerm2` | iterm2 |
| `KITTY_WINDOW_ID` | kitty |
| `TERM=xterm-kitty` | kitty |
| `WEZTERM_UNIX_SOCKET` | iterm2 (WezTerm поддерживает оба) |
| `COLORTERM=truecolor` | unicode (fallback) |
| иначе | none |

### Фаза 2: Рендереры (4 функции)

```typescript
function renderITerm2(imagePath: string, maxCols: number): string
function renderKitty(imagePath: string, maxCols: number): string
function renderSixel(imagePath: string, maxCols: number): string
function renderUnicode(imagePath: string, maxCols: number): string
```

Каждая функция:
1. Читает файл изображения
2. Определяет размеры (через `image-size` npm-пакет или `sips` на macOS)
3. Масштабирует под ширину терминала (maxCols символов)
4. Возвращает escape-последовательность для вывода

Зависимости:
- `image-size` — определение размеров PNG/JPEG/GIF без декодирования
- `sharp` (опционально) — ресайз для unicode-рендера

### Фаза 3: Интеграция в Chat.tsx

В компоненте `Chat`:
- После Ctrl+V / `/image` — показать inline-превью в чате
- В `ToolCallBlock` — если tool вернул изображение, показать inline
- В markdown-рендере — если встречается `![alt](path)`, показать inline

```tsx
// В Chat.tsx, при отображении attachments
{attachments.map((path) => (
  <Text key={path}>{renderInlineImage(path, terminalCols)}</Text>
))}
```

### Фаза 4: Ресайз и адаптация

- Запрос размеров терминала: `process.stdout.columns` (cols) + `process.stdout.rows` (rows)
- Запрос pixel-размеров ячейки: escape `\033[14t` → ответ `\033[4;<H>;<W>t`
- Адаптация при ресайзе: `process.stdout.on('resize', ...)`
- Максимальная ширина изображения: `min(imageWidth, cols - 4)` символов

### Фаза 5: Кеширование

- Кешировать отрендеренные изображения в `.runtime/image-cache/`
- Ключ: `sha256(filePath + protocol + maxCols)`
- TTL: 1 час
- Очистка при старте (как clipboard cache)

## Приоритет протоколов

1. **iTerm2** — самый распространённый на macOS (наш основной кейс)
2. **Kitty** — растущая популярность, лучший протокол
3. **Unicode half-block** — fallback для всех truecolor-терминалов
4. **Sixel** — низкий приоритет (мало терминалов поддерживают)

## Оценка трудозатрат

| Фаза | Файлы | Строк | Сложность |
|---|---|---|---|
| 1. Детект | 1 | ~60 | Низкая |
| 2. Рендереры | 1 | ~200 | Средняя |
| 3. Интеграция | 2 | ~40 | Низкая |
| 4. Ресайз | 1 | ~30 | Низкая |
| 5. Кеширование | 1 | ~50 | Низкая |
| **Итого** | **4-5** | **~380** | **Средняя** |

## Зависимости (npm)

- `image-size` (~5KB) — размеры изображений без декодирования
- `sharp` (опционально, ~30MB) — ресайз для unicode-рендера
  - Альтернатива: `sips` (встроен в macOS, бесплатно)

## Что НЕ делаем

- Генерацию изображений (это фича провайдера, не TUI)
- OCR / анализ изображений (это фича провайдера)
- Видеопоток (не поддерживается ни одним терминальным протоколом)
- Animated GIF в sixel (слишком сложно для ROI)