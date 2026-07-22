import { useEffect } from 'react';

const ENTER_ALT = '\x1b[?1049h';
const EXIT_ALT = '\x1b[?1049l';
const CLEAR = '\x1b[2J\x1b[H';
const HIDE_CURSOR = '\x1b[?25l';
const SHOW_CURSOR = '\x1b[?25h';
const ENABLE_MOUSE = '\x1b[?1000h\x1b[?1006h';
const DISABLE_MOUSE = '\x1b[?1000l\x1b[?1006l';

/** Входит в alternate screen buffer + включает mouse reporting. */
export function useFullscreen(enabled = true) {
  useEffect(() => {
    if (!enabled || !process.stdout.isTTY) return;

    process.stdout.write(ENTER_ALT + CLEAR + HIDE_CURSOR + ENABLE_MOUSE);

    return () => {
      process.stdout.write(DISABLE_MOUSE + SHOW_CURSOR + EXIT_ALT);
    };
  }, [enabled]);
}

/** Очищает экран и ставит курсор в (0,0). */
export function clearScreen() {
  process.stdout.write(CLEAR);
}

/** Перемещает курсор в (row, col) — 1-based. */
export function moveCursor(row: number, col: number) {
  process.stdout.write(`\x1b[${row};${col}H`);
}

/** Скрывает курсор. */
export function hideCursor() {
  process.stdout.write(HIDE_CURSOR);
}

/** Показывает курсор. */
export function showCursor() {
  process.stdout.write(SHOW_CURSOR);
}