import { Transform, type TransformCallback } from 'node:stream';
import { EventEmitter } from 'node:events';

export interface ParsedMouseEvent {
  type: 'press' | 'release' | 'scroll';
  button: 'left' | 'right' | 'middle' | 'scroll-up' | 'scroll-down';
  col: number;
  row: number;
}

/**
 * Transform-стрим который фильтрует SGR mouse escape-последовательности
 * из stdin ДО того как Ink их увидит. Mouse-события парсятся и эмитятся
 * через EventEmitter, а чистые данные (клавиши) проходят в Ink.
 *
 * Это решает конфликт useMouse/useInput — Ink получает только клавиши,
 * мы получаем только мышь.
 */
export class MouseFilterStream extends Transform {
  readonly mouse = new EventEmitter();
  private buffer = '';

  constructor() {
    super({ encoding: 'utf-8' });

    // Проксируем TTY-свойства на реальный stdin — Ink требует setRawMode
    const realStdin = process.stdin;
    Object.defineProperty(this, 'isTTY', { get: () => realStdin.isTTY });
    Object.defineProperty(this, 'isRaw', { get: () => realStdin.isRaw });
    (this as unknown as Record<string, unknown>).setRawMode = (mode: boolean) => {
      if (realStdin.isTTY && realStdin.setRawMode) {
        realStdin.setRawMode(mode);
      }
      return this;
    };
    // Проксируем columns/rows (из stdout — там точно есть)
    Object.defineProperty(this, 'columns', { get: () => process.stdout.columns });
    Object.defineProperty(this, 'rows', { get: () => process.stdout.rows });
  }

  _transform(chunk: Buffer, _encoding: string, callback: TransformCallback): void {
    this.buffer += chunk.toString();

    // SGR mouse: \x1b[<button;col;rowM или \x1b[<button;col;rowm
    // Также старые форматы: \x1b[M... (3 байта после M)
    const sgrRegex = /\x1b\[<(\d+);(\d+);(\d+)([Mm])/g;
    let clean = '';
    let lastIndex = 0;
    let match;

    while ((match = sgrRegex.exec(this.buffer)) !== null) {
      // Всё до этого match — чистые данные (клавиши)
      clean += this.buffer.slice(lastIndex, match.index);
      lastIndex = match.index + match[0].length;

      // Парсим mouse event
      const buttonCode = parseInt(match[1]);
      const col = parseInt(match[2]);
      const row = parseInt(match[3]);
      const isRelease = match[4] === 'm';

      let button: ParsedMouseEvent['button'];
      let type: ParsedMouseEvent['type'];

      if (buttonCode === 64) { button = 'scroll-up'; type = 'scroll'; }
      else if (buttonCode === 65) { button = 'scroll-down'; type = 'scroll'; }
      else if (buttonCode === 0) { button = 'left'; type = isRelease ? 'release' : 'press'; }
      else if (buttonCode === 1) { button = 'middle'; type = isRelease ? 'release' : 'press'; }
      else if (buttonCode === 2) { button = 'right'; type = isRelease ? 'release' : 'press'; }
      else continue;

      this.mouse.emit('event', { type, button, col, row } satisfies ParsedMouseEvent);
    }

    // Остаток после последнего match
    clean += this.buffer.slice(lastIndex);

    // Проверяем незавершённую escape-последовательность в конце буфера
    // (может быть начало mouse sequence разрезанное по чанкам)
    const escIdx = clean.lastIndexOf('\x1b');
    if (escIdx !== -1 && escIdx > clean.length - 20) {
      const tail = clean.slice(escIdx);
      // Если хвост похож на начало mouse sequence — буферизуем
      if (/^\x1b\[<?[\d;]*$/.test(tail)) {
        this.buffer = tail;
        clean = clean.slice(0, escIdx);
      } else {
        this.buffer = '';
      }
    } else {
      this.buffer = '';
    }

    if (clean) {
      this.push(clean);
    }
    callback();
  }

  _flush(callback: TransformCallback): void {
    if (this.buffer) {
      this.push(this.buffer);
      this.buffer = '';
    }
    callback();
  }
}