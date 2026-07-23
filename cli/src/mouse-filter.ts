import { Transform, type TransformCallback } from 'node:stream';
import { EventEmitter } from 'node:events';

export interface ParsedMouseEvent {
  type: 'press' | 'release' | 'scroll';
  button: 'left' | 'right' | 'middle' | 'scroll-up' | 'scroll-down';
  col: number;
  row: number;
}

/**
 * Transform-стрим:
 * 1. Фильтрует SGR mouse → EventEmitter (клики/скролл)
 * 2. Детектит hold-space на уровне потока → voice events
 * 3. Проксирует TTY-методы на реальный stdin
 * 4. Клавиши → Ink
 */
export class MouseFilterStream extends Transform {
  readonly mouse = new EventEmitter();
  readonly voice = new EventEmitter();
  private buf = '';
  private spaceCount = 0;
  private lastSpaceTime = 0;
  private voiceMode = false;
  private voiceLastSpace = 0;
  private spaceTimer: ReturnType<typeof setTimeout> | null = null;
  private pasting = false; // bracketed paste — отключаем hold-space

  constructor() {
    super();
    const real = process.stdin;
    Object.defineProperty(this, 'isTTY', { get: () => real.isTTY });
    Object.defineProperty(this, 'isRaw', { get: () => real.isRaw });
    (this as any).setRawMode = (m: boolean) => { real.setRawMode?.(m); return this; };
    (this as any).ref = () => { real.ref?.(); return this; };
    (this as any).unref = () => { real.unref?.(); return this; };
    (this as any).resume = () => { real.resume(); return this; };
    (this as any).pause = () => { real.pause(); return this; };
    Object.defineProperty(this, 'columns', { get: () => process.stdout.columns });
    Object.defineProperty(this, 'rows', { get: () => process.stdout.rows });
  }

  stopVoice() { this.voiceMode = false; this.spaceCount = 0; }
  startVoice() { this.voiceMode = true; this.spaceCount = 0; this.voiceLastSpace = 0; }

  _transform(chunk: Buffer, _enc: string, cb: TransformCallback): void {
    this.buf += chunk.toString();
    let out = '';
    let i = 0;

    while (i < this.buf.length) {
      const ch = this.buf[i];

      // SGR mouse: \x1b[<btn;col;rowM/m
      if (ch === '\x1b' && this.buf[i + 1] === '[' && this.buf[i + 2] === '<') {
        let seqEnd = -1;
        for (let j = i + 3; j < this.buf.length; j++) {
          if (this.buf[j] === 'M' || this.buf[j] === 'm') { seqEnd = j; break; }
        }
        if (seqEnd === -1) { this.buf = this.buf.slice(i); if (out) this.push(out); cb(); return; }
        const seq = this.buf.slice(i, seqEnd + 1);
        const m = seq.match(/\x1b\[<(\d+);(\d+);(\d+)([Mm])/);
        if (m) {
          const bc = parseInt(m[1]);
          // Shift+mouse (bc & 4) → пропускаем для нативного выделения терминалом
          if (bc & 4) {
            out += seq; // пропускаем в Ink/терминал
            i = seqEnd + 1;
            continue;
          }
          const col = parseInt(m[2]), row = parseInt(m[3]), rel = m[4] === 'm';
          let btn: ParsedMouseEvent['button'], type: ParsedMouseEvent['type'];
          if (bc === 64) { btn = 'scroll-up'; type = 'scroll'; }
          else if (bc === 65) { btn = 'scroll-down'; type = 'scroll'; }
          else if (bc <= 2) { btn = (['left','middle','right'] as const)[bc]; type = rel ? 'release' : 'press'; }
          else { btn = 'left'; type = rel ? 'release' : 'press'; }
          this.mouse.emit('event', { type, button: btn, col, row });
        }
        i = seqEnd + 1;
        continue;
      }

      // Bracketed paste: \x1b[200~ ... \x1b[201~
      if (ch === '\x1b' && this.buf.slice(i, i + 6) === '\x1b[200~') {
        this.pasting = true;
        out += this.buf.slice(i, i + 6);
        i += 6;
        continue;
      }
      if (ch === '\x1b' && this.buf.slice(i, i + 6) === '\x1b[201~') {
        this.pasting = false;
        this.spaceCount = 0; // сброс hold detection после paste
        out += this.buf.slice(i, i + 6);
        i += 6;
        continue;
      }

      // Space — hold detection на уровне потока (НО НЕ во время paste!)
      if (ch === ' ' && !this.voiceMode && !this.pasting) {
        const now = Date.now();
        this.spaceCount = (now - this.lastSpaceTime < 120) ? this.spaceCount + 1 : 1;
        this.lastSpaceTime = now;
        if (this.spaceCount >= 4) {
          this.voiceMode = true;
          this.spaceCount = 0;
          this.voiceLastSpace = now;
          this.voice.emit('hold-start');
          i++; continue; // НЕ отправляем пробел
        }
        out += ch; // отправляем пробел сразу
        if (this.spaceTimer) clearTimeout(this.spaceTimer);
        this.spaceTimer = setTimeout(() => { this.spaceCount = 0; }, 200);
        i++; continue;
      }

      // Space во время voice mode
      if (ch === ' ' && this.voiceMode) {
        const now = Date.now();
        if (now - this.voiceLastSpace > 250) {
          this.voiceMode = false;
          this.spaceCount = 0;
          this.voice.emit('stop');
        }
        this.voiceLastSpace = now;
        i++; continue; // НЕ отправляем пробел в Ink
      }

      // Ctrl+C во время voice = стоп
      if (ch === '\x03' && this.voiceMode) {
        this.voiceMode = false;
        this.spaceCount = 0;
        this.voice.emit('stop');
        i++; continue;
      }

      out += ch;
      i++;
    }
    this.buf = '';
    if (out) this.push(out);
    cb();
  }

  _flush(cb: TransformCallback): void {
    if (this.buf) { this.push(this.buf); this.buf = ''; }
    cb();
  }
}