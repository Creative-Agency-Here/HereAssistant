import { useEffect, useRef } from 'react';

export interface MouseEvent {
  type: 'press' | 'release' | 'scroll';
  button: 'left' | 'right' | 'middle' | 'scroll-up' | 'scroll-down';
  col: number;
  row: number;
}

type MouseHandler = (event: MouseEvent) => void;

/** Слушает mouse-события из MouseFilterStream (через globalThis.__ha_mouse).
 *  Mouse-escape-последовательности отфильтрованы ДО Ink — конфликта нет. */
export function useMouse(handler: MouseHandler, enabled = true) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!enabled) return;

    const emitter = (globalThis as Record<string, unknown>).__ha_mouse as
      | { on: (e: string, fn: (ev: MouseEvent) => void) => void; off: (e: string, fn: (ev: MouseEvent) => void) => void }
      | undefined;

    if (!emitter) return;

    const listener = (ev: MouseEvent) => handlerRef.current(ev);
    emitter.on('event', listener);
    return () => { emitter.off('event', listener); };
  }, [enabled]);
}