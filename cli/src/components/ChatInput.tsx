import React, { useState, useRef, useEffect, useCallback } from 'react';
import { EventEmitter } from 'node:events';
import { Box, Text, useInput, type DOMElement } from 'ink';
import { copyTextToClipboard, pasteImageFromClipboard } from '../clipboard.js';
import { openInEditor } from '../editor.js';
import { voiceRealtime, canRealtimeVoice } from '../voice.js';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type { MouseEvent } from '../hooks/useMouse.js';
import type { ParsedEscapeKey, ParsedNavigationKey } from '../mouse-filter.js';

// Подробный лог клавиш включается только явным opt-in: он может содержать ввод.
const DEBUG_LOG = '/tmp/ha-keys.log';
function dbg(msg: string) {
  if (process.env.HA_TUI_DEBUG_KEYS !== '1') return;
  try { fs.appendFileSync(DEBUG_LOG, `${new Date().toISOString()} ${msg}\n`); } catch {}
}

const REC_FRAMES = ['●', '◉', '◎', '◉'];
const WAVE_FRAMES = ['▁▃▅▇', '▃▅▇▅', '▅▇▅▃', '▇▅▃▁', '▅▃▁▃', '▃▁▃▅'];

function elementPosition(node: DOMElement): { left: number; top: number } {
  let left = 0;
  let top = 0;
  let current: DOMElement | undefined = node;
  while (current) {
    left += current.yogaNode?.getComputedLeft() ?? 0;
    top += current.yogaNode?.getComputedTop() ?? 0;
    current = current.parentNode;
  }
  return { left, top };
}

type InputPoint = { line: number; col: number };
type InputSelection = { anchor: InputPoint; focus: InputPoint };

function compareInputPoints(a: InputPoint, b: InputPoint): number {
  return a.line === b.line ? a.col - b.col : a.line - b.line;
}

function normalizedInputSelection(selection: InputSelection): [InputPoint, InputPoint] {
  return compareInputPoints(selection.anchor, selection.focus) <= 0
    ? [selection.anchor, selection.focus]
    : [selection.focus, selection.anchor];
}

export function selectedInputText(lines: string[], selection: InputSelection): string {
  const [start, end] = normalizedInputSelection(selection);
  if (compareInputPoints(start, end) === 0) return '';
  if (start.line === end.line) {
    return (lines[start.line] ?? '').slice(start.col, end.col);
  }

  const selected = [(lines[start.line] ?? '').slice(start.col)];
  for (let line = start.line + 1; line < end.line; line++) {
    selected.push(lines[line] ?? '');
  }
  selected.push((lines[end.line] ?? '').slice(0, end.col));
  return selected.join('\n');
}

function selectedRangeForLine(
  selection: InputSelection | null,
  line: number,
  lineLength: number,
): [number, number] | null {
  if (!selection) return null;
  const [start, end] = normalizedInputSelection(selection);
  if (compareInputPoints(start, end) === 0 || line < start.line || line > end.line) return null;

  const from = line === start.line ? Math.min(start.col, lineLength) : 0;
  const to = line === end.line ? Math.min(end.col, lineLength) : lineLength;
  return from < to ? [from, to] : null;
}

const SLASH_COMMANDS = [
  '/help', '/model', '/account', '/status', '/resume', '/rename', '/fork', '/search', '/bg',
  '/theme', '/archive', '/delete', '/mcp', '/copy', '/img', '/nl', '/image', '/diff', '/new', '/compact', '/exit',
];

interface Props {
  onSubmit: (value: string) => void;
  onImagePaste?: (path: string) => void;
  onShellCommand?: (cmd: string) => void;
  onRemoveAttachment?: (index: number) => void;
  attachments?: string[];
  disabled?: boolean;
  placeholder?: string;
  cwd?: string;
  onSelectionCopy?: (text: string) => boolean | void;
}

export function ChatInput({ onSubmit, onImagePaste, onShellCommand, onRemoveAttachment, attachments = [], disabled = false, placeholder, cwd, onSelectionCopy }: Props) {
  const [lines, setLines] = useState<string[]>(['']);
  const [cursorLine, setCursorLine] = useState(0);
  const [cursorCol, setCursorCol] = useState(0);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [showComplete, setShowComplete] = useState(false);
  const [completeIdx, setCompleteIdx] = useState(0);
  const [recording, setRecording] = useState(false);
  const [voiceText, setVoiceText] = useState('');
  const [recFrame, setRecFrame] = useState(0);
  const [recSeconds, setRecSeconds] = useState(0);
  const draftRef = useRef<string | null>(null);
  const voiceRef = useRef<{ kill: () => void } | null>(null);
  const holdRef = useRef({ count: 0, lastTime: 0, recLastSpace: 0 });
  const recordingRef = useRef(false);
  const cursorColRef = useRef(0);
  const cursorLineRef = useRef(0);
  const lastEscapeRef = useRef(0);
  const pasteGuardRef = useRef(0); // защита от race condition при paste
  const pendingSubmitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputLinesRef = useRef<DOMElement | null>(null);
  const [selection, setSelection] = useState<InputSelection | null>(null);
  const selectionRef = useRef<InputSelection | null>(null);
  const dragAnchorRef = useRef<InputPoint | null>(null);

  const updateSelection = useCallback((next: InputSelection | null) => {
    selectionRef.current = next;
    setSelection(next);
  }, []);

  const copySelection = useCallback((): boolean => {
    const current = selectionRef.current;
    if (!current) return false;
    const selected = selectedInputText(lines, current);
    if (!selected || !selected.trim()) return false;
    const copied = onSelectionCopy
      ? onSelectionCopy(selected) !== false
      : copyTextToClipboard(selected);
    return copied;
  }, [lines, onSelectionCopy]);

  const moveCursor = useCallback((line: number, col: number) => {
    cursorLineRef.current = line;
    cursorColRef.current = col;
    setCursorLine(line);
    setCursorCol(col);
  }, []);

  const cancelPendingSubmit = useCallback(() => {
    if (pendingSubmitTimerRef.current) {
      clearTimeout(pendingSubmitTimerRef.current);
      pendingSubmitTimerRef.current = null;
    }
  }, []);

  const clearDraft = useCallback(() => {
    cancelPendingSubmit();
    setLines(['']);
    moveCursor(0, 0);
    updateSelection(null);
    setHistoryIdx(-1);
    draftRef.current = null;
    setShowComplete(false);
  }, [cancelPendingSubmit, moveCursor, updateSelection]);

  const insertNewline = useCallback((removeBackslash = false) => {
    cancelPendingSubmit();
    setLines((prev) => {
      const lineIdx = Math.min(cursorLineRef.current, prev.length - 1);
      const line = prev[lineIdx] ?? '';
      const col = Math.min(cursorColRef.current, line.length);
      const newLines = [...prev];
      const splitCol = removeBackslash && col > 0 ? col - 1 : col;
      newLines[lineIdx] = line.slice(0, splitCol);
      newLines.splice(lineIdx + 1, 0, line.slice(col));
      moveCursor(lineIdx + 1, 0);
      return newLines;
    });
  }, [cancelPendingSubmit, moveCursor]);

  useEffect(() => () => cancelPendingSubmit(), [cancelPendingSubmit]);

  // Нормализованные newline/Escape events из MouseFilterStream.
  useEffect(() => {
    const keysEmitter = (globalThis as any).__ha_keys as EventEmitter | undefined;
    if (!keysEmitter || disabled) return;
    const onNewline = () => insertNewline();
    const onEscape = (event: ParsedEscapeKey = { modifiers: 1 }) => {
      const hasShift = ((event.modifiers - 1) & 1) !== 0;
      if (hasShift) return;
      if (lines.some((line) => line.length > 0)) clearDraft();
    };
    keysEmitter.on('newline-key', onNewline);
    keysEmitter.on('escape-key', onEscape);
    return () => {
      keysEmitter.off('newline-key', onNewline);
      keysEmitter.off('escape-key', onEscape);
    };
  }, [disabled, insertNewline, lines, clearDraft]);

  // Стрелки через custom events (Ink не парсит \x1b[D из Transform stream)
  useEffect(() => {
    const keysEmitter = (globalThis as any).__ha_keys as EventEmitter | undefined;
    if (!keysEmitter || disabled) return;
    const onArrow = (ev: ParsedNavigationKey) => {
      const { direction, modifiers } = ev;
      const lineIdx = cursorLineRef.current;
      const col = cursorColRef.current;
      const line = lines[lineIdx] ?? '';
      const modifierBits = Math.max(0, modifiers - 1);
      const hasSuper = (modifierBits & 8) !== 0;
      const jumpsByWord = (modifierBits & (2 | 4)) !== 0;

      if (direction === 'home') {
        moveCursor(lineIdx, 0); return;
      }
      if (direction === 'end') {
        moveCursor(lineIdx, line.length); return;
      }
      if (direction === 'left') {
        if (hasSuper) {
          moveCursor(lineIdx, 0);
        } else if (jumpsByWord) { // Option/Ctrl+Left = word left
          const before = line.slice(0, col);
          const m = before.match(/\S+\s*$/);
          const nc = m ? col - m[0].length : 0;
          moveCursor(lineIdx, nc);
        } else if (col > 0) {
          moveCursor(lineIdx, col - 1);
        } else if (lineIdx > 0) {
          const prevLen = (lines[lineIdx - 1] ?? '').length;
          moveCursor(lineIdx - 1, prevLen);
        }
        return;
      }
      if (direction === 'right') {
        if (hasSuper) {
          moveCursor(lineIdx, line.length);
        } else if (jumpsByWord) { // Option/Ctrl+Right = word right
          const after = line.slice(col);
          const m = after.match(/^\s*\S+/);
          const nc = m ? col + m[0].length : line.length;
          moveCursor(lineIdx, nc);
        } else if (col < line.length) {
          moveCursor(lineIdx, col + 1);
        } else if (lineIdx < lines.length - 1) {
          moveCursor(lineIdx + 1, 0);
        }
        return;
      }
      if (direction === 'up') {
        if (lineIdx > 0) {
          const prevLen = (lines[lineIdx - 1] ?? '').length;
          const nc = Math.min(col, prevLen);
          moveCursor(lineIdx - 1, nc);
        } else if (history.length > 0) {
          const newIdx = historyIdx < history.length - 1 ? historyIdx + 1 : historyIdx;
          if (historyIdx === -1) draftRef.current = lines.join('\n');
          setHistoryIdx(newIdx);
          setText(history[newIdx]);
        }
        return;
      }
      if (direction === 'down') {
        if (lineIdx < lines.length - 1) {
          const nextLen = (lines[lineIdx + 1] ?? '').length;
          const nc = Math.min(col, nextLen);
          moveCursor(lineIdx + 1, nc);
        } else if (historyIdx > 0) {
          const newIdx = historyIdx - 1;
          setHistoryIdx(newIdx);
          setText(history[newIdx]);
        } else if (historyIdx === 0) {
          setHistoryIdx(-1);
          setText(draftRef.current ?? '');
        }
        return;
      }
    };
    keysEmitter.on('arrow-key', onArrow);
    return () => { keysEmitter.off('arrow-key', onArrow); };
  }, [disabled, lines, history, historyIdx, moveCursor]);

  // Синхронизация ref → state, НО НЕ во время быстрого paste
  useEffect(() => {
    if (Date.now() - pasteGuardRef.current < 300) return; // paste идёт — не трогаем ref
    cursorColRef.current = cursorCol;
    cursorLineRef.current = cursorLine;
  }, [cursorCol, cursorLine]);

  // Voice events из MouseFilterStream (hold-space detection на уровне потока)
  useEffect(() => {
    const voiceEmitter = (globalThis as any).__ha_voice as EventEmitter | undefined;
    if (!voiceEmitter) return;

    const onHoldStart = () => {
      dbg('voice: hold-start from stream');
      setRecording(true);
      recordingRef.current = true;
      setVoiceText('');
      // Убираем лишние пробелы из hold (последние 4+)
      setLines((prev) => {
        const newLines = [...prev];
        const lineIdx = Math.min(cursorLineRef.current, newLines.length - 1);
        const line = newLines[lineIdx] ?? '';
        const col = Math.min(cursorColRef.current, line.length);
        // Убираем до 4 пробелов перед курсором
        let removeStart = col;
        while (removeStart > 0 && line[removeStart - 1] === ' ' && col - removeStart < 5) removeStart--;
        newLines[lineIdx] = line.slice(0, removeStart) + line.slice(col);
        moveCursor(lineIdx, removeStart);
        return newLines;
      });
      voiceRef.current = voiceRealtime(120, (p) => setVoiceText(p), (f) => setVoiceText(f));
    };

    const onStop = () => {
      dbg('voice: stop from stream');
      voiceRef.current?.kill();
      voiceRef.current = null;
      setRecording(false);
      recordingRef.current = false;
      setVoiceText((vt) => {
        if (vt.trim()) {
          setLines((prev) => {
            const newLines = [...prev];
            const lineIdx = Math.min(cursorLineRef.current, newLines.length - 1);
            const line = newLines[lineIdx] ?? '';
            const col = Math.min(cursorColRef.current, line.length);
            const insert = (col > 0 && line[col - 1] !== ' ' ? ' ' : '') + vt.trim();
            newLines[lineIdx] = line.slice(0, col) + insert + line.slice(col);
            moveCursor(lineIdx, col + insert.length);
            return newLines;
          });
        }
        return '';
      });
    };

    voiceEmitter.on('hold-start', onHoldStart);
    voiceEmitter.on('stop', onStop);
    return () => { voiceEmitter.off('hold-start', onHoldStart); voiceEmitter.off('stop', onStop); };
  }, [moveCursor]);

  // Ctrl+M = ручной toggle голос (альтернатива hold-space)
  useEffect(() => {
    // handled in useInput below
  }, []);

  useEffect(() => {
    const SIGNAL_FILE = '/tmp/ha-clipboard-paste';
    const check = setInterval(() => {
      // 1. Глобальный insert (от /img команды)
      const pending = (globalThis as Record<string, unknown>).__ha_insert as string | undefined;
      if (pending) {
        (globalThis as Record<string, unknown>).__ha_insert = undefined;
        insertText(pending);
        return;
      }
      // 2. Файл-сигнал от расширения (Cmd+V с картинкой)
      try {
        if (fs.existsSync(SIGNAL_FILE)) {
          const imgPath = fs.readFileSync(SIGNAL_FILE, 'utf-8').trim();
          fs.unlinkSync(SIGNAL_FILE);
          if (imgPath && fs.existsSync(imgPath) && onImagePaste) {
            const imgIdx = attachments.length + 1;
            const tag = `[Image #${imgIdx}]`;
            insertText(tag);
            onImagePaste(imgPath);
          }
        }
      } catch { /* ignore */ }
    }, 100);
    return () => clearInterval(check);
  }, [cursorLine, cursorCol, attachments, onImagePaste]);

  const insertText = (text: string) => {
    setLines((prev) => {
      const newLines = [...prev];
      const line = newLines[cursorLineRef.current] ?? '';
      const col = Math.min(cursorColRef.current, line.length);
      const sep = col > 0 && line[col - 1] !== ' ' ? ' ' : '';
      newLines[cursorLineRef.current] = line.slice(0, col) + sep + text + ' ' + line.slice(col);
      moveCursor(cursorLineRef.current, col + sep.length + text.length + 1);
      return newLines;
    });
  };

  // Анимация записи + sync ref
  useEffect(() => {
    recordingRef.current = recording;
    if (!recording) { setRecFrame(0); setRecSeconds(0); return; }
    const frameTimer = setInterval(() => setRecFrame((f) => (f + 1) % REC_FRAMES.length), 200);
    const waveTimer = setInterval(() => setRecSeconds((s) => s + 1), 1000);
    return () => { clearInterval(frameTimer); clearInterval(waveTimer); };
  }, [recording]);

  const termRows = process.stdout.rows || 24;
  const maxInputLines = Math.max(3, Math.min(lines.length, Math.floor(termRows / 3)));
  const safeCursorLine = Math.max(0, Math.min(cursorLine, lines.length - 1));
  const safeCursorCol = Math.max(0, Math.min(cursorCol, (lines[safeCursorLine] ?? '').length));
  const startLine = Math.max(
    0,
    Math.min(safeCursorLine - maxInputLines + 1, lines.length - maxInputLines),
  );

  useEffect(() => {
    const emitter = (globalThis as Record<string, unknown>).__ha_mouse as
      | { on: (e: string, fn: (ev: MouseEvent) => void) => void; off: (e: string, fn: (ev: MouseEvent) => void) => void }
      | undefined;
    if (!emitter) return;

    const pointFromMouse = (ev: MouseEvent, clampOutside: boolean): InputPoint | null => {
      if (disabled || recording) return null;
      const inputNode = inputLinesRef.current;
      if (!inputNode) return null;

      const { left, top } = elementPosition(inputNode);
      const mouseCol = ev.col - 1;
      const mouseRow = ev.row - 1;
      if (!clampOutside && (mouseRow < top || mouseRow >= top + maxInputLines)) return null;
      const visibleRow = Math.max(0, Math.min(mouseRow - top, maxInputLines - 1));

      const lineIdx = Math.max(
        0,
        Math.min(startLine + visibleRow, lines.length - 1),
      );
      const col = Math.max(
        0,
        Math.min(mouseCol - left, (lines[lineIdx] ?? '').length),
      );
      return { line: lineIdx, col };
    };

    const hitTest = (ev: MouseEvent) => dragAnchorRef.current !== null || pointFromMouse(ev, false) !== null;
    const copyCurrentSelection = () => copySelection();
    const globals = globalThis as Record<string, unknown>;
    globals.__ha_input_mouse_hit = hitTest;
    globals.__ha_input_copy_selection = copyCurrentSelection;

    const handler = (ev: MouseEvent) => {
      if (disabled || recording || ev.button !== 'left') return;

      if (ev.type === 'press') {
        const point = pointFromMouse(ev, false);
        if (!point) return;
        dragAnchorRef.current = point;
        updateSelection({ anchor: point, focus: point });
        moveCursor(point.line, point.col);
        return;
      }

      const anchor = dragAnchorRef.current;
      if (!anchor) return;
      const point = pointFromMouse(ev, true) ?? anchor;
      const next = { anchor, focus: point };
      updateSelection(next);
      moveCursor(point.line, point.col);

      if (ev.type === 'release') {
        dragAnchorRef.current = null;
        if (compareInputPoints(anchor, point) === 0) {
          updateSelection(null);
          return;
        }
      }
    };

    emitter.on('event', handler);
    return () => {
      emitter.off('event', handler);
      if (globals.__ha_input_mouse_hit === hitTest) delete globals.__ha_input_mouse_hit;
      if (globals.__ha_input_copy_selection === copyCurrentSelection) delete globals.__ha_input_copy_selection;
    };
  }, [disabled, recording, lines, maxInputLines, startLine, moveCursor, updateSelection, copySelection, onSelectionCopy]);

  const currentLine = lines[cursorLine] ?? '';
  const isSlash = lines.length === 1 && currentLine.startsWith('/');

  // @file автодополнение
  const atMatch = currentLine.match(/@([^\s]*)$/);
  const isAt = !!atMatch && cwd;
  const atPrefix = atMatch ? atMatch[1] : '';

  const getFileCompletions = (): string[] => {
    if (!cwd || !isAt) return [];
    try {
      const searchDir = atPrefix.includes('/')
        ? path.resolve(cwd, path.dirname(atPrefix))
        : cwd;
      const prefix = atPrefix.includes('/')
        ? path.basename(atPrefix)
        : atPrefix;
      if (!fs.existsSync(searchDir)) return [];
      const entries = fs.readdirSync(searchDir, { withFileTypes: true });
      return entries
        .filter((e) => !e.name.startsWith('.') && e.name.startsWith(prefix))
        .slice(0, 10)
        .map((e) => {
          const rel = atPrefix.includes('/')
            ? path.join(path.dirname(atPrefix), e.name)
            : e.name;
          return `@${rel}${e.isDirectory() ? '/' : ''}`;
        });
    } catch { return []; }
  };

  const slashCompletions = isSlash
    ? SLASH_COMMANDS.filter((c) => c.startsWith(currentLine) && c !== currentLine)
    : [];
  const fileCompletions = isAt ? getFileCompletions() : [];
  const completions = [...slashCompletions, ...fileCompletions];

  const text = lines.join('\n');

  useEffect(() => {
    const globals = globalThis as Record<string, unknown>;
    const hasText = () => lines.some((line) => line.length > 0);
    globals.__ha_input_has_text = hasText;
    return () => {
      if (globals.__ha_input_has_text === hasText) delete globals.__ha_input_has_text;
    };
  }, [lines]);

  const setText = (value: string) => {
    const newLines = value.split('\n');
    const line = newLines.length - 1;
    const col = (newLines[line] ?? '').length;
    setLines(newLines);
    moveCursor(line, col);
  };

  const handleSubmit = (rawValue = text) => {
    cancelPendingSubmit();
    const value = rawValue.trim();
    if (!value) return;

    // Shell mode: ! команда
    if (value.startsWith('!') && onShellCommand) {
      const cmd = value.slice(1).trim();
      if (cmd) {
        setHistory((h) => (h[0] === value ? h : [value, ...h].slice(0, 100)));
        setHistoryIdx(-1);
        draftRef.current = null;
        setLines(['']);
        moveCursor(0, 0);
        setShowComplete(false);
        onShellCommand(cmd);
        return;
      }
    }

    setHistory((h) => (h[0] === value ? h : [value, ...h].slice(0, 100)));
    setHistoryIdx(-1);
    draftRef.current = null;
    setLines(['']);
    moveCursor(0, 0);
    setShowComplete(false);
    onSubmit(value);
  };

  useInput((input, key) => {
    if (disabled) return;

    if (key.meta && input.toLowerCase() === 'c' && copySelection()) return;
    if (selectionRef.current && (input || key.backspace || key.delete || key.leftArrow || key.rightArrow || key.upArrow || key.downArrow)) {
      updateSelection(null);
    }

    // DEBUG: логируем каждую клавишу
    dbg(`KEY input=${JSON.stringify(input)} charCode=${input?.charCodeAt(0)} left=${key.leftArrow} right=${key.rightArrow} up=${key.upArrow} down=${key.downArrow} escape=${key.escape} ctrl=${key.ctrl} meta=${key.meta} shift=${key.shift} return=${key.return} pageUp=${key.pageUp} pageDown=${key.pageDown} tab=${key.tab} backspace=${key.backspace} delete=${key.delete}`);

    // Пробел и голос теперь обрабатываются в MouseFilterStream (уровень потока)
    // Ctrl+M = ручной toggle голос
    if (key.ctrl && input === 'm') {
      const filter = (globalThis as any).__ha_filter;
      if (recordingRef.current) {
        filter?.stopVoice();
      } else if (canRealtimeVoice()) {
        filter?.startVoice();
      }
      return;
    }

    const isPlainEscape = key.escape
      && !key.return
      && !key.leftArrow
      && !key.rightArrow
      && !key.upArrow
      && !key.downArrow;
    const isAltEnterContinuation = key.return && Date.now() - lastEscapeRef.current < 200;

    // Fallback для stdin без MouseFilterStream; основной путь — escape-key выше.
    if (isPlainEscape) {
      lastEscapeRef.current = Date.now();
      if (key.shift) return;
      if (text.length > 0) clearDraft();
      return;
    }

    // Новая строка: Shift+Enter / Cmd+Enter / Alt+Enter / Ctrl+Enter / Escape+Enter / Ctrl+J
    const isModifiedEnter = key.return && (key.shift || key.meta || key.ctrl || key.escape);
    const isAltEnterViaEscape = isAltEnterContinuation;
    const isCtrlEnterRaw = input === '\n' || (key.ctrl && input === 'j');

    // \+Enter → newline (как в Claude Code / Gemini CLI)
    const refLine = lines[cursorLineRef.current] ?? '';
    const refCol = Math.min(cursorColRef.current, refLine.length);
    const isBackslashEnter = key.return && refCol > 0 && refLine[refCol - 1] === '\\';

    // Enter во время bracketed paste → newline (не submit)
    const isPasteEnter = key.return && (globalThis as any).__ha_filter?.isPasting;

    if (isModifiedEnter || isAltEnterViaEscape || isCtrlEnterRaw || isBackslashEnter || isPasteEnter) {
      lastEscapeRef.current = 0;
      insertNewline(isBackslashEnter);
      return;
    }

    // Enter без модификаторов: ждём 50 мс, чтобы отличить двойной Enter.
    if (key.return) {
      if (showComplete && completions.length > 0) {
        setText(completions[completeIdx]);
        setShowComplete(false);
        return;
      }
      if (!text.trim()) return;
      if (pendingSubmitTimerRef.current) {
        insertNewline();
        return;
      }
      const pendingValue = text;
      pendingSubmitTimerRef.current = setTimeout(() => {
        pendingSubmitTimerRef.current = null;
        handleSubmit(pendingValue);
      }, 50);
      return;
    }

    // Любой другой ввод до истечения окна отменяет отложенный submit.
    cancelPendingSubmit();

    // Tab — autocomplete
    if (key.tab && completions.length > 0) {
      const selected = completions[completeIdx];
      if (isAt) {
        // Заменяем @prefix на выбранный файл
        const newLines = [...lines];
        newLines[cursorLine] = currentLine.replace(/@[^\s]*$/, selected);
        setLines(newLines);
      } else {
        setText(selected);
      }
      setShowComplete(false);
      return;
    }

    // Up/Down — history or cursor movement
    if (key.upArrow) {
      if (isSlash && completions.length > 0) {
        setShowComplete(true);
        setCompleteIdx((i) => Math.max(0, i - 1));
        return;
      }
      if (cursorLine > 0) {
        const prevLine = lines[cursorLine - 1] ?? '';
        moveCursor(cursorLine - 1, Math.min(cursorCol, prevLine.length));
        return;
      }
      if (history.length > 0) {
        const newIdx = historyIdx < history.length - 1 ? historyIdx + 1 : historyIdx;
        if (historyIdx === -1) draftRef.current = text;
        setHistoryIdx(newIdx);
        setText(history[newIdx]);
      }
      return;
    }

    if (key.downArrow) {
      if (isSlash && completions.length > 0) {
        setShowComplete(true);
        setCompleteIdx((i) => Math.min(completions.length - 1, i + 1));
        return;
      }
      if (cursorLine < lines.length - 1) {
        const nextLine = lines[cursorLine + 1] ?? '';
        moveCursor(cursorLine + 1, Math.min(cursorCol, nextLine.length));
        return;
      }
      if (historyIdx > 0) {
        const newIdx = historyIdx - 1;
        setHistoryIdx(newIdx);
        setText(history[newIdx]);
      } else if (historyIdx === 0) {
        setHistoryIdx(-1);
        setText(draftRef.current ?? '');
      }
      return;
    }

    // Backspace — cursor updates ВЫНЕSEНЫ из setLines (React nested setState = race)
    if (key.backspace || key.delete) {
      if (text.trim() === '' && attachments.length > 0 && onRemoveAttachment) {
        onRemoveAttachment(attachments.length - 1);
        return;
      }
      const lineIdx = cursorLineRef.current;
      const col = cursorColRef.current;
      const line = lines[lineIdx] ?? '';
      const c = Math.min(col, line.length);
      const before = line.slice(0, c);
      const tagMatch = before.match(/\[Image #\d+\]\s?$/);

      let newLineIdx = lineIdx;
      let newCol = c;

      if (tagMatch) {
        const removeLen = tagMatch[0].length;
        newCol = c - removeLen;
        setLines((prev) => {
          const newLines = [...prev];
          const l = prev[lineIdx] ?? '';
          newLines[lineIdx] = l.slice(0, c - removeLen) + l.slice(c);
          return newLines;
        });
        if (onRemoveAttachment && attachments.length > 0) onRemoveAttachment(attachments.length - 1);
      } else if (c > 0) {
        newCol = c - 1;
        setLines((prev) => {
          const newLines = [...prev];
          const l = prev[lineIdx] ?? '';
          newLines[lineIdx] = l.slice(0, c - 1) + l.slice(c);
          return newLines;
        });
      } else if (lineIdx > 0) {
        const prevLen = (lines[lineIdx - 1] ?? '').length;
        newLineIdx = lineIdx - 1;
        newCol = prevLen;
        setLines((prev) => {
          const newLines = [...prev];
          newLines[lineIdx - 1] = (prev[lineIdx - 1] ?? '') + (prev[lineIdx] ?? '');
          newLines.splice(lineIdx, 1);
          return newLines;
        });
      }

      moveCursor(newLineIdx, newCol);
      setShowComplete(false);
      return;
    }

    // Ctrl+G — внешний редактор
    if (key.ctrl && input === 'g') {
      const edited = openInEditor(text);
      if (edited !== null) setText(edited);
      return;
    }

    // Ctrl+I = вставка изображения inline (Ctrl+V перехватывается терминалом/VS Code)
    if (key.ctrl && (input === 'i' || input === '\t')) {
      if (onImagePaste) {
        const imgPath = pasteImageFromClipboard();
        if (imgPath) {
          const imgIdx = attachments.length + 1;
          const tag = `[Image #${imgIdx}]`;
          const col = Math.min(cursorCol, currentLine.length);
          const newLines = [...lines];
          const sep = col > 0 && currentLine[col - 1] !== ' ' ? ' ' : '';
          newLines[cursorLine] = currentLine.slice(0, col) + sep + tag + ' ' + currentLine.slice(col);
          setLines(newLines);
          moveCursor(cursorLine, col + sep.length + tag.length + 1);
          onImagePaste(imgPath);
          return;
        }
      }
      return;
    }

    // Ctrl+V / Cmd+V — сначала картинка, потом текст
    if ((key.ctrl || key.meta) && (input === 'v' || input === '\x16')) {
      const col = cursorColRef.current;
      const lineIdx = cursorLineRef.current;
      dbg(`paste: ctrl=${key.ctrl} meta=${key.meta} col=${col} line=${lineIdx} lines=${JSON.stringify(lines)}`);
      // Сначала пробуем изображение
      if (onImagePaste) {
        const imgPath = pasteImageFromClipboard();
        if (imgPath) {
          dbg(`paste: image found`);
          const imgIdx = attachments.length + 1;
          const tag = `[Image #${imgIdx}]`;
          setLines((prev) => {
            const newLines = [...prev];
            const line = newLines[lineIdx] ?? '';
            const c = Math.min(col, line.length);
            const sep = c > 0 && line[c - 1] !== ' ' ? ' ' : '';
            newLines[lineIdx] = line.slice(0, c) + sep + tag + ' ' + line.slice(c);
            dbg(`paste: after insert lineIdx=${lineIdx} c=${c} result=${JSON.stringify(newLines)}`);
            moveCursor(lineIdx, c + sep.length + tag.length + 1);
            return newLines;
          });
          onImagePaste(imgPath);
          return;
        }
      }
      // Fallback: текст — но проверяем не путь ли это к картинке
      try {
        let clipText = execSync('pbpaste 2>/dev/null', { encoding: 'utf-8', timeout: 2000 }).trim();
        if (clipText) {
          // Детект file:// URL или абсолютного пути к изображению
          let filePath = clipText;
          if (filePath.startsWith('file://')) filePath = decodeURIComponent(filePath.replace('file://', ''));
          const isImagePath = /\.(png|jpe?g|gif|webp|svg|tiff?|bmp)$/i.test(filePath) && fs.existsSync(filePath);

          if (isImagePath && onImagePaste) {
            dbg(`paste: file path detected: ${filePath}`);
            const imgIdx = attachments.length + 1;
            const tag = `[Image #${imgIdx}]`;
            setLines((prev) => {
              const newLines = [...prev];
              const line = newLines[lineIdx] ?? '';
              const c = Math.min(col, line.length);
              const sep = c > 0 && line[c - 1] !== ' ' ? ' ' : '';
              newLines[lineIdx] = line.slice(0, c) + sep + tag + ' ' + line.slice(c);
              moveCursor(lineIdx, c + sep.length + tag.length + 1);
              return newLines;
            });
            onImagePaste(filePath);
            return;
          }

          dbg(`paste: text ${clipText.length} chars`);
          // Нормализуем переносы и разбиваем на строки
          const normalized = clipText.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
          const pasteLines = normalized.split('\n');
          setLines((prev) => {
            const lineIdx = cursorLineRef.current;
            const line = prev[lineIdx] ?? '';
            const c = Math.min(cursorColRef.current, line.length);
            const newLines = [...prev];

            if (pasteLines.length === 1) {
              // Однострочный paste
              newLines[lineIdx] = line.slice(0, c) + pasteLines[0] + line.slice(c);
              moveCursor(lineIdx, c + pasteLines[0].length);
            } else {
              // Многострочный paste: разбиваем текущую строку
              const before = line.slice(0, c);
              const after = line.slice(c);
              newLines[lineIdx] = before + pasteLines[0];
              for (let pi = 1; pi < pasteLines.length - 1; pi++) {
                newLines.splice(lineIdx + pi, 0, pasteLines[pi]);
              }
              newLines.splice(lineIdx + pasteLines.length - 1, 0, pasteLines[pasteLines.length - 1] + after);
              const newLineIdx = lineIdx + pasteLines.length - 1;
              const newCol = pasteLines[pasteLines.length - 1].length;
              moveCursor(newLineIdx, newCol);
            }
            return newLines;
          });
        }
      } catch { dbg(`paste: pbpaste failed`); }
      return;
    }

    // Ctrl+U — clear line
    if (key.ctrl && input === 'u') {
      const newLines = [...lines];
      newLines[cursorLine] = '';
      setLines(newLines);
      moveCursor(cursorLine, 0);
      return;
    }

    // Ctrl+K — delete to end of line
    if (key.ctrl && input === 'k') {
      const newLines = [...lines];
      newLines[cursorLine] = currentLine.slice(0, cursorCol);
      setLines(newLines);
      moveCursor(cursorLine, Math.min(cursorCol, newLines[cursorLine].length));
      return;
    }

    // Ctrl+W — delete word
    if (key.ctrl && input === 'w') {
      const newLines = [...lines];
      const before = currentLine.slice(0, cursorCol);
      const after = currentLine.slice(cursorCol);
      const kept = before.replace(/\S+\s*$/, '');
      newLines[cursorLine] = kept + after;
      setLines(newLines);
      moveCursor(cursorLine, kept.length);
      return;
    }

    // Left/Right/Up/Down/Home/End обрабатываются через arrow-key custom events (useEffect выше)
    // useInput не получает стрелки от Ink через Transform stream

    // Regular character input — functional update + немедленный ref update
    // (фикс для Cmd+V paste: символы приходят быстрее чем React рендерит)
    if (input && !key.ctrl && !key.meta && input !== '\r' && input !== '\n') {
      pasteGuardRef.current = Date.now(); // блокируем useEffect sync
      setLines((prev) => {
        const newLines = [...prev];
        const lineIdx = cursorLineRef.current;
        const line = newLines[lineIdx] ?? '';
        const col = Math.min(cursorColRef.current, line.length);
        newLines[lineIdx] = line.slice(0, col) + input + line.slice(col);
        const newCol = col + input.length;
        cursorColRef.current = newCol; // немедленно!
        setCursorCol(newCol);
        return newLines;
      });
      setShowComplete(isSlash && completions.length > 0);
    }
  }, { isActive: !disabled });

  if (disabled) {
    return (
      <Box paddingX={1}>
        <Text dimColor>агент работает…</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column">
      {showComplete && completions.length > 0 && (
        <Box flexDirection="column" paddingX={1}>
          {completions.map((c, i) => (
            <Text key={c} color={i === completeIdx ? 'cyan' : undefined} bold={i === completeIdx}>
              {i === completeIdx ? '❯ ' : '  '}{c}
            </Text>
          ))}
        </Box>
      )}
      {/* Attachment chips убраны — теги inline в тексте */}
      <Box paddingX={1} flexDirection="column">
        {recording && (
          <Box>
            <Text color="red" bold>{REC_FRAMES[recFrame]} </Text>
            <Text color="red">{WAVE_FRAMES[recFrame % WAVE_FRAMES.length]} </Text>
            <Text color="red">{voiceText || 'слушаю…'}</Text>
            <Text color="red"> ▌</Text>
            <Text dimColor>  {recSeconds}с · пробел — стоп</Text>
          </Box>
        )}
        {recording ? (
          <Box>
            <Text color="magenta" bold>› </Text>
            <Text>{text}</Text>
            <Text color="red">{voiceText ? ' ' + voiceText : ''}</Text>
            <Text color="red">▌</Text>
          </Box>
        ) : (
          <Box>
            <Text color="magenta" bold>› </Text>
            <Box
              key={`input-${lines.length}-${safeCursorLine}-${safeCursorCol}`}
              ref={inputLinesRef}
              flexDirection="column"
            >
              {(() => {
                const visibleLines = lines.slice(startLine, startLine + maxInputLines);
                // Заполнители: Ink не очищает строки при уменьшении высоты → артефакты
                while (visibleLines.length < maxInputLines) visibleLines.push('\x00');
                return visibleLines.map((line, vi) => {
                  const i = startLine + vi;
                  const isFiller = line === '\x00';
                  if (isFiller) return <Text key={`filler-${vi}`} dimColor>{' '}</Text>;
                  const col = i === safeCursorLine ? Math.min(safeCursorCol, line.length) : line.length;
                  const before = line.slice(0, col);
                  const after = line.slice(col);
                  const selectedRange = selectedRangeForLine(selection, i, line.length);
                  const renderWithTags = (s: string) => {
                    if (!s) return [<Text key={0}>{''}</Text>];
                    const parts = s.split(/(\[Image #\d+\])/g);
                    return parts.map((p, j) =>
                      p.startsWith('[Image') ? <Text key={j} color="cyan" bold>{p}</Text> : <Text key={j}>{p}</Text>
                    );
                  };
                  // Пустая строка 0 без текста — placeholder + курсор
                  const showPlaceholder = line.length === 0 && i === 0 && safeCursorLine === 0 && safeCursorCol === 0;
                  const isEmpty = line.length === 0 && i !== safeCursorLine;
                  return (
                    <Text key={i}>
                      {showPlaceholder ? <>
                        <Text dimColor>{placeholder ?? 'сообщение… (Ctrl+M — голос, /img — фото, ! shell)'}</Text>
                        <Text color="magenta">▌</Text>
                      </> : isEmpty ? <Text dimColor>{' '}</Text> : <>
                        {selectedRange ? <>
                          {renderWithTags(line.slice(0, selectedRange[0]))}
                          <Text inverse>{line.slice(selectedRange[0], selectedRange[1])}</Text>
                          {renderWithTags(line.slice(selectedRange[1]))}
                        </> : <>
                          {renderWithTags(before)}
                          {i === safeCursorLine && <Text color="magenta">▌</Text>}
                          {renderWithTags(after)}
                        </>}
                      </>}
                    </Text>
                  );
                });
              })()}
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
}
