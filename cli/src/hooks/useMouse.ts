import { useEffect, useRef } from 'react';
import { useStdin } from 'ink';

export interface MouseEvent {
  type: 'press' | 'release' | 'scroll';
  button: 'left' | 'right' | 'middle' | 'scroll-up' | 'scroll-down';
  col: number;
  row: number;
}

type MouseHandler = (event: MouseEvent) => void;

/** Парсит SGR mouse events из stdin (через Ink's useStdin, без конфликта). */
export function useMouse(handler: MouseHandler, enabled = true) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  const { stdin, setRawMode } = useStdin();

  useEffect(() => {
    if (!enabled || !stdin) return;

    const parseBuffer = (data: Buffer) => {
      const str = data.toString();
      const regex = /\x1b\[<(\d+);(\d+);(\d+)([Mm])/g;
      let match;
      while ((match = regex.exec(str)) !== null) {
        const buttonCode = parseInt(match[1]);
        const col = parseInt(match[2]);
        const row = parseInt(match[3]);
        const isRelease = match[4] === 'm';

        let button: MouseEvent['button'];
        let type: MouseEvent['type'];

        if (buttonCode === 64) { button = 'scroll-up'; type = 'scroll'; }
        else if (buttonCode === 65) { button = 'scroll-down'; type = 'scroll'; }
        else if (buttonCode === 0) { button = 'left'; type = isRelease ? 'release' : 'press'; }
        else if (buttonCode === 1) { button = 'middle'; type = isRelease ? 'release' : 'press'; }
        else if (buttonCode === 2) { button = 'right'; type = isRelease ? 'release' : 'press'; }
        else continue;

        handlerRef.current({ type, button, col, row });
      }
    };

    stdin.on('data', parseBuffer);
    return () => { stdin.removeListener('data', parseBuffer); };
  }, [enabled, stdin]);
}