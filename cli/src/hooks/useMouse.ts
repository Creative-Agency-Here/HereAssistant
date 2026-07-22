import { useEffect, useRef, useCallback } from 'react';

export interface MouseEvent {
  type: 'press' | 'release' | 'scroll';
  button: 'left' | 'right' | 'middle' | 'scroll-up' | 'scroll-down';
  col: number; // 1-based
  row: number; // 1-based
}

type MouseHandler = (event: MouseEvent) => void;

/** Парсит SGR mouse events из stdin. */
export function useMouse(handler: MouseHandler, enabled = true) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  const parseBuffer = useCallback((data: string) => {
    // SGR format: \x1b[<button;col;rowM or \x1b[<button;col;rowm
    const regex = /\x1b\[<(\d+);(\d+);(\d+)([Mm])/g;
    let match;
    while ((match = regex.exec(data)) !== null) {
      const buttonCode = parseInt(match[1]);
      const col = parseInt(match[2]);
      const row = parseInt(match[3]);
      const isRelease = match[4] === 'm';

      let button: MouseEvent['button'];
      let type: MouseEvent['type'];

      if (buttonCode === 64) {
        button = 'scroll-up';
        type = 'scroll';
      } else if (buttonCode === 65) {
        button = 'scroll-down';
        type = 'scroll';
      } else if (buttonCode === 0) {
        button = 'left';
        type = isRelease ? 'release' : 'press';
      } else if (buttonCode === 1) {
        button = 'middle';
        type = isRelease ? 'release' : 'press';
      } else if (buttonCode === 2) {
        button = 'right';
        type = isRelease ? 'release' : 'press';
      } else {
        continue;
      }

      handlerRef.current({ type, button, col, row });
    }
  }, []);

  useEffect(() => {
    if (!enabled || !process.stdin.isTTY) return;

    // Set stdin to raw mode to receive mouse events
    const wasRaw = process.stdin.isRaw;
    process.stdin.setRawMode(true);
    process.stdin.resume();

    const onData = (data: Buffer) => {
      parseBuffer(data.toString());
    };

    process.stdin.on('data', onData);

    return () => {
      process.stdin.removeListener('data', onData);
      if (!wasRaw) process.stdin.setRawMode(false);
    };
  }, [enabled, parseBuffer]);
}