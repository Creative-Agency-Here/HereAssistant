import { Transform, type TransformCallback } from 'node:stream';
import { EventEmitter } from 'node:events';

export interface ParsedMouseEvent {
  type: 'press' | 'move' | 'release' | 'scroll';
  button: 'left' | 'right' | 'middle' | 'scroll-up' | 'scroll-down';
  col: number;
  row: number;
}

export interface ParsedNavigationKey {
  direction: 'left' | 'right' | 'up' | 'down' | 'home' | 'end';
  modifiers: number;
}

export interface ParsedEscapeKey {
  modifiers: number;
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
  readonly keys = new EventEmitter();
  private buf = '';
  private spaceCount = 0;
  private lastSpaceTime = 0;
  private voiceMode = false;
  private voiceLastSpace = 0;
  private spaceTimer: ReturnType<typeof setTimeout> | null = null;
  private escapeTimer: ReturnType<typeof setTimeout> | null = null;
  private pasting = false; // bracketed paste — отключаем hold-space
  private rapidInputUntil = 0; // детект быстрого ввода (paste без bracketed)

  constructor() {
    super();
    const real = process.stdin;
    Object.defineProperty(this, 'isTTY', { get: () => real.isTTY });
    Object.defineProperty(this, 'isRaw', { get: () => real.isRaw });
    (this as any).setRawMode = (m: boolean) => { real.setRawMode?.(m); return this; };
    (this as any).ref = () => { real.ref?.(); return this; };
    (this as any).unref = () => { real.unref?.(); return this; };
    Object.defineProperty(this, 'columns', { get: () => process.stdout.columns });
    Object.defineProperty(this, 'rows', { get: () => process.stdout.rows });
  }

  stopVoice() { this.voiceMode = false; this.spaceCount = 0; }
  startVoice() { this.voiceMode = true; this.spaceCount = 0; this.voiceLastSpace = 0; }
  get isPasting() { return this.pasting; }

  private emitNavigationKey(terminator: string, modifiers = 1): boolean {
    const directions: Record<string, ParsedNavigationKey['direction']> = {
      A: 'up',
      B: 'down',
      C: 'right',
      D: 'left',
      H: 'home',
      F: 'end',
    };
    const direction = directions[terminator.toUpperCase()];
    if (!direction) return false;
    this.keys.emit('arrow-key', { direction, modifiers } satisfies ParsedNavigationKey);
    return true;
  }

  _transform(chunk: Buffer, _enc: string, cb: TransformCallback): void {
    let incoming = chunk.toString();
    // ESC и Enter могут прийти разными chunks: ждём короткое продолжение,
    // чтобы отличить обычный Escape от legacy Alt+Enter.
    if (this.escapeTimer) {
      clearTimeout(this.escapeTimer);
      this.escapeTimer = null;
      if (incoming[0] === '\r' || incoming[0] === '\n') {
        this.keys.emit('newline-key', { modifiers: 3 });
        if (incoming[0] === '\r' && incoming[1] === '\n') incoming = incoming.slice(2);
        else incoming = incoming.slice(1);
      } else {
        incoming = '\x1b' + incoming;
      }
    }

    this.buf += incoming;
    let out = '';
    let i = 0;

    // Детект быстрого ввода: chunk > 5 printable символов = paste
    const printable = this.buf.replace(/\x1b\[[^a-zA-Z]*[a-zA-Z]/g, '').replace(/[\x00-\x1f]/g, '');
    if (printable.length > 5) {
      this.rapidInputUntil = Date.now() + 500;
    }

    while (i < this.buf.length) {
      const ch = this.buf[i];

      // Legacy Alt+Enter одним chunk: ESC + CR/LF.
      if (ch === '\x1b' && (this.buf[i + 1] === '\r' || this.buf[i + 1] === '\n')) {
        this.keys.emit('newline-key', { modifiers: 3 });
        i += 2;
        if (this.buf[i - 1] === '\r' && this.buf[i] === '\n') i++;
        continue;
      }

      // SS3 navigation keys: Terminal.app и некоторые xterm-профили.
      if (ch === '\x1b' && this.buf[i + 1] === 'O') {
        if (i + 2 >= this.buf.length) {
          this.buf = this.buf.slice(i);
          if (out) this.push(out);
          cb();
          return;
        }
        if (this.emitNavigationKey(this.buf[i + 2])) {
          i += 3;
          continue;
        }
      }

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
          // Терминалы обычно сами оставляют Shift+drag для нативного выделения.
          // Если последовательность всё же пришла приложению, поглощаем её.
          if (bc & 4) {
            i = seqEnd + 1;
            continue;
          }
          const col = parseInt(m[2]), row = parseInt(m[3]), rel = m[4] === 'm';
          let btn: ParsedMouseEvent['button'], type: ParsedMouseEvent['type'];
          if (bc === 64) { btn = 'scroll-up'; type = 'scroll'; }
          else if (bc === 65) { btn = 'scroll-down'; type = 'scroll'; }
          else {
            const buttonCode = bc & 3;
            btn = (['left', 'middle', 'right', 'left'] as const)[buttonCode];
            type = rel ? 'release' : (bc & 32) ? 'move' : 'press';
          }
          this.mouse.emit('event', { type, button: btn, col, row });
        }
        i = seqEnd + 1;
        continue;
      }

      // Фильтр CSI-последовательностей
      if (ch === '\x1b' && this.buf[i + 1] === '[' && this.buf[i + 2] !== '<') {
        let csiEnd = -1;
        for (let j = i + 2; j < this.buf.length; j++) {
          const c = this.buf.charCodeAt(j);
          if (c >= 0x40 && c <= 0x7e) { csiEnd = j; break; }
        }
        if (csiEnd === -1) {
          this.buf = this.buf.slice(i);
          if (out) this.push(out);
          cb();
          return;
        }
        const csiSeq = this.buf.slice(i, csiEnd + 1);
        const inner = csiSeq.slice(2, -1); // между [ и terminator
        const terminator = csiSeq[csiSeq.length - 1];

        // Bracketed paste — НЕ пропускаем в Ink (иначе [200~ протекает как текст)
        if (csiSeq === '\x1b[200~') { this.pasting = true; (globalThis as any).__ha_pasting = true; i = csiEnd + 1; continue; }
        if (csiSeq === '\x1b[201~') { this.pasting = false; (globalThis as any).__ha_pasting = false; this.spaceCount = 0; i = csiEnd + 1; continue; }

        // В CSI буква направления — terminator, а не inner:
        // \x1b[D => inner="", terminator="D".
        if ((inner === '' || /^\d+$/.test(inner)) && this.emitNavigationKey(terminator)) {
          i = csiEnd + 1;
          continue;
        }

        // Модифицированные стрелки/Home/End → custom event
        const modifiedNavigation = inner.match(/^\d+;(\d+)$/);
        if (modifiedNavigation && this.emitNavigationKey(terminator, parseInt(modifiedNavigation[1], 10))) {
          i = csiEnd + 1;
          continue;
        }

        // CSI с ~ терминатором (Delete, PageUp/Down, Insert, Home/End, модифицированные)
        if (terminator === '~') {
          // Option+Delete → удалить слово назад. Проверка должна идти до общих Delete.
          if (inner === '3;3') {
            out += '\x17'; // Ctrl+W
            i = csiEnd + 1;
            continue;
          }
          // Cmd+Delete / Shift+Delete на macOS: \x1b[3;2~ или \x1b[27;2;127~ → Ctrl+U (удалить до начала строки)
          if (inner === '3;2' || inner === '27;2;127') {
            out += '\x15'; // Ctrl+U
            i = csiEnd + 1;
            continue;
          }
          // Остальные ~ (Delete, PageUp/Down, Insert, Home/End) — пропускаем в Ink
          out += csiSeq;
          i = csiEnd + 1;
          continue;
        }

        // Kitty keyboard protocol (CSI u): \x1b[{codepoint};{modifiers}u
        if (terminator === 'u' && /^\d+(;\d+)?$/.test(inner)) {
          const parts = inner.split(';');
          const codepoint = parseInt(parts[0], 10);
          const modifiers = parts[1] ? parseInt(parts[1], 10) : 1;
          const modifierBits = Math.max(0, modifiers - 1);
          const hasSuper = (modifierBits & 8) !== 0;
          // Kitty protocol: Cmd+C (Super+C) → явное копирование HA-selection.
          if (hasSuper && String.fromCodePoint(codepoint).toLowerCase() === 'c') {
            this.keys.emit('copy-key');
            i = csiEnd + 1;
            continue;
          }
          // Enter (13) с модификаторами → newline
          if (codepoint === 13 && modifiers > 1) {
            this.keys.emit('newline-key', { modifiers });
            i = csiEnd + 1;
            continue;
          }
          // Tab (9) с модификаторами → пропускаем как обычный Tab
          if (codepoint === 9) { out += '\t'; i = csiEnd + 1; continue; }
          // Backspace (127) с модификаторами
          if (codepoint === 127) { out += '\x7f'; i = csiEnd + 1; continue; }
          // Escape (27) с модификаторами → отдельное событие, чтобы Shift+Esc
          // можно было отличить от обычного Escape.
          if (codepoint === 27) {
            this.keys.emit('escape-key', { modifiers } satisfies ParsedEscapeKey);
            i = csiEnd + 1;
            continue;
          }
          // Printable ASCII с модификаторами (Ctrl+key и т.д.)
          if (codepoint >= 32 && codepoint <= 126) {
            if (modifiers >= 5 && modifiers <= 8) {
              // Ctrl+key: стандартная формула ASCII control chars
              const ctrlChar = String.fromCharCode(codepoint & 0x1f);
              out += ctrlChar;
            } else {
              out += String.fromCharCode(codepoint);
            }
            i = csiEnd + 1;
            continue;
          }
          // Kitty navigation keys идут напрямую в общий обработчик:
          // повторно пропущенный CSI stock Ink всё равно не распознаёт.
          const KITTY_NAVIGATION: Record<number, ParsedNavigationKey['direction']> = {
            57350: 'left',
            57351: 'right',
            57352: 'up',
            57353: 'down',
            57354: 'home',
            57355: 'end',
          };
          const KITTY_FUNC_TILDE: Record<number, string> = {
            57356: '5', 57357: '6', 57358: '2', 57359: '3', // PgUp, PgDn, Ins, Del
          };
          if (KITTY_NAVIGATION[codepoint]) {
            this.keys.emit('arrow-key', {
              direction: KITTY_NAVIGATION[codepoint],
              modifiers,
            } satisfies ParsedNavigationKey);
            i = csiEnd + 1;
            continue;
          }
          if (KITTY_FUNC_TILDE[codepoint]) {
            const num = KITTY_FUNC_TILDE[codepoint];
            out += modifiers > 1 ? `\x1b[${num};${modifiers}~` : `\x1b[${num}~`;
            i = csiEnd + 1;
            continue;
          }
          // Остальные CSI u — дропаем
          i = csiEnd + 1;
          continue;
        }

        // Всё остальное (модифицированный Enter/Tab/F-keys): DROP
        i = csiEnd + 1;
        continue;
      }

      // Внутри bracketed paste CR/LF должны стать переносом строки, а не submit.
      // Нормализуем их в Ctrl+J, который ChatInput однозначно трактует как newline.
      if (this.pasting && (ch === '\r' || ch === '\n')) {
        out += '\n';
        if (ch === '\r' && this.buf[i + 1] === '\n') i++;
        i++;
        continue;
      }

      // Одиночный Escape: на 30 мс ждём Enter-продолжение, затем создаём
      // отдельное событие вместо передачи неоднозначного байта в Ink.
      if (ch === '\x1b' && i + 1 >= this.buf.length) {
        this.buf = '';
        if (out) this.push(out);
        this.escapeTimer = setTimeout(() => {
          this.escapeTimer = null;
          this.keys.emit('escape-key', { modifiers: 1 } satisfies ParsedEscapeKey);
        }, 30);
        cb();
        return;
      }

      // Alt+прочая клавиша — оставляем Ink.
      if (ch === '\x1b' && this.buf[i + 1] !== '[' && this.buf[i + 1] !== 'O') {
        out += ch;
        i++;
        continue;
      }

      // Space — hold detection (НО НЕ во время paste или rapid input!)
      if (ch === ' ' && !this.voiceMode && !this.pasting && Date.now() > this.rapidInputUntil) {
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
    if (this.escapeTimer) {
      clearTimeout(this.escapeTimer);
      this.escapeTimer = null;
      this.keys.emit('escape-key', { modifiers: 1 } satisfies ParsedEscapeKey);
    }
    if (this.buf) { this.push(this.buf); this.buf = ''; }
    cb();
  }
}
