import { useEffect } from 'react';

const ENABLE_MOUSE = '\x1b[?1000h\x1b[?1006h';
const DISABLE_MOUSE = '\x1b[?1000l\x1b[?1006l';

/** Включает mouse reporting для кликабельных элементов.
 * НЕ входит в alternate screen — Ink сам управляет рендерингом. */
export function useFullscreen(enabled = true) {
  useEffect(() => {
    if (!enabled || !process.stdout.isTTY) return;

    process.stdout.write(ENABLE_MOUSE);

    return () => {
      process.stdout.write(DISABLE_MOUSE);
    };
  }, [enabled]);
}