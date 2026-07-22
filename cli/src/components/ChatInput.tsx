import React, { useState, useRef, useEffect } from 'react';
import { Box, Text, useInput } from 'ink';
import { pasteImageFromClipboard } from '../clipboard.js';
import { openInEditor } from '../editor.js';
import { voiceRealtime, canRealtimeVoice } from '../voice.js';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type { MouseEvent } from '../hooks/useMouse.js';

const REC_FRAMES = ['●', '◉', '◎', '◉'];
const WAVE_FRAMES = ['▁▃▅▇', '▃▅▇▅', '▅▇▅▃', '▇▅▃▁', '▅▃▁▃', '▃▁▃▅'];

const SLASH_COMMANDS = [
  '/help', '/model', '/account', '/status', '/resume', '/rename', '/fork', '/search', '/bg',
  '/theme', '/archive', '/delete', '/mcp', '/copy', '/image', '/diff', '/new', '/compact', '/exit',
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
}

export function ChatInput({ onSubmit, onImagePaste, onShellCommand, onRemoveAttachment, attachments = [], disabled = false, placeholder, cwd }: Props) {
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
  const spaceHoldRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Анимация записи
  useEffect(() => {
    if (!recording) { setRecFrame(0); setRecSeconds(0); return; }
    const frameTimer = setInterval(() => setRecFrame((f) => (f + 1) % REC_FRAMES.length), 200);
    const waveTimer = setInterval(() => setRecSeconds((s) => s + 1), 1000);
    return () => { clearInterval(frameTimer); clearInterval(waveTimer); };
  }, [recording]);

  // Mouse: клик двигает курсор в поле ввода
  const termRows = process.stdout.rows || 24;

  useEffect(() => {
    const emitter = (globalThis as Record<string, unknown>).__ha_mouse as
      | { on: (e: string, fn: (ev: MouseEvent) => void) => void; off: (e: string, fn: (ev: MouseEvent) => void) => void }
      | undefined;
    if (!emitter) return;

    const PREFIX = 4; // paddingX(1) + '› '(2) + 1-based→0-based(1)
    const inputRow = termRows - 1;

    const handler = (ev: MouseEvent) => {
      if (disabled || recording) return;
      if (ev.type !== 'press' || ev.button !== 'left') return;
      // Клик в строке ввода (последняя строка перед нижней рамкой)
      if (ev.row < inputRow - lines.length || ev.row > inputRow) return;

      const lineIdx = Math.min(ev.row - (inputRow - lines.length + 1), lines.length - 1);
      const col = Math.max(0, Math.min(ev.col - PREFIX, (lines[lineIdx] ?? '').length));
      setCursorLine(Math.max(0, lineIdx));
      setCursorCol(col);
    };

    emitter.on('event', handler);
    return () => { emitter.off('event', handler); };
  }, [disabled, recording, lines, termRows]);

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

  const setText = (value: string) => {
    const newLines = value.split('\n');
    setLines(newLines);
    setCursorLine(newLines.length - 1);
    setCursorCol((newLines[newLines.length - 1] ?? '').length);
  };

  const handleSubmit = () => {
    const value = text.trim();
    if (!value) return;

    // Shell mode: ! команда
    if (value.startsWith('!') && onShellCommand) {
      const cmd = value.slice(1).trim();
      if (cmd) {
        setHistory((h) => (h[0] === value ? h : [value, ...h].slice(0, 100)));
        setHistoryIdx(-1);
        draftRef.current = null;
        setLines(['']);
        setCursorLine(0);
        setShowComplete(false);
        onShellCommand(cmd);
        return;
      }
    }

    setHistory((h) => (h[0] === value ? h : [value, ...h].slice(0, 100)));
    setHistoryIdx(-1);
    draftRef.current = null;
    setLines(['']);
    setCursorLine(0);
    setShowComplete(false);
    onSubmit(value);
  };

  useInput((input, key) => {
    if (disabled) return;

    // Пробел = печатает пробел ИЛИ включает голос при зажатии (как Claude Code)
    // Логика: каждый пробел сразу печатается. Если повторы сыпятся (hold) —
    // через 3 повтора включается голос и лишние пробелы стираются.
    if (input === ' ' && !key.ctrl && !key.meta && !key.shift) {
      if (recording) {
        // В режиме записи: пробел = стоп (короткое нажатие)
        // Игнорируем key repeat во время записи
        if (spaceHoldRef.current) clearTimeout(spaceHoldRef.current);
        spaceHoldRef.current = setTimeout(() => {
          // 200ms без повторов = короткое нажатие = стоп
          voiceRef.current?.kill();
          voiceRef.current = null;
          setRecording(false);
          spaceHoldRef.current = null;
          if (voiceText.trim()) {
            const current = text;
            const sep = current && !current.endsWith(' ') ? ' ' : '';
            setText(current + sep + voiceText.trim());
            setVoiceText('');
          }
        }, 200);
        return;
      }

      // Печатаем пробел СРАЗУ (как обычно)
      const col = Math.min(cursorCol, currentLine.length);
      const newLines = [...lines];
      newLines[cursorLine] = currentLine.slice(0, col) + ' ' + currentLine.slice(col);
      setLines(newLines);
      setCursorCol(col + 1);

      // Трекаем повторы для hold detection
      const now = Date.now();
      const lastSpace = (spaceHoldRef.current as unknown as number) || 0;
      if (now - lastSpace < 150) {
        // Повторный пробел в течение 150ms = key repeat (зажатие)
        const count = ((spaceHoldRef as unknown as Record<string, number>)._count || 0) + 1;
        (spaceHoldRef as unknown as Record<string, number>)._count = count;
        if (count >= 3 && canRealtimeVoice()) {
          // 3+ повтора = зажал → стираем лишние пробелы и включаем голос
          const removeCount = count + 1; // все пробелы из hold
          const line = newLines[cursorLine];
          const removeStart = Math.max(0, col + 1 - removeCount);
          newLines[cursorLine] = line.slice(0, removeStart) + line.slice(col + 1);
          setLines(newLines);
          setCursorCol(removeStart);
          // Включаем запись
          setRecording(true);
          setVoiceText('');
          (spaceHoldRef as unknown as Record<string, number>)._count = 0;
          voiceRef.current = voiceRealtime(120, (partial) => {
            setVoiceText(partial);
          }, (final) => {
            setVoiceText(final);
          });
        }
      } else {
        (spaceHoldRef as unknown as Record<string, number>)._count = 1;
      }
      (spaceHoldRef.current as unknown as number) = now;
      // Сброс счётчика через 300ms без повторов
      setTimeout(() => {
        (spaceHoldRef as unknown as Record<string, number>)._count = 0;
      }, 300);
      return;
    }

    // Ctrl+M = тоже toggle голос (альтернатива)
    if (key.ctrl && input === 'm') {
      if (recording) {
        voiceRef.current?.kill();
        voiceRef.current = null;
        setRecording(false);
        if (voiceText.trim()) {
          const current = text;
          const sep = current && !current.endsWith(' ') ? ' ' : '';
          setText(current + sep + voiceText.trim());
          setVoiceText('');
        }
      } else if (canRealtimeVoice()) {
        setRecording(true);
        setVoiceText('');
        voiceRef.current = voiceRealtime(120, (partial) => {
          setVoiceText(partial);
        }, (final) => {
          setVoiceText(final);
        });
      }
      return;
    }

    // Enter — submit (single line) or newline (multiline with Alt)
    if (key.return) {
      if (showComplete && completions.length > 0) {
        setText(completions[completeIdx]);
        setShowComplete(false);
        return;
      }
      handleSubmit();
      return;
    }

    // Alt+Enter or Escape+Enter — insert newline
    if (key.escape && input === '\r') {
      const newLines = [...lines];
      newLines.splice(cursorLine + 1, 0, '');
      setLines(newLines);
      setCursorLine(cursorLine + 1);
      return;
    }

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
        setCursorLine(cursorLine - 1);
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
        setCursorLine(cursorLine + 1);
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

    // Backspace
    if (key.backspace || key.delete) {
      // Пустой ввод + есть аттачменты → удалить последний
      if (text.trim() === '' && attachments.length > 0 && onRemoveAttachment) {
        onRemoveAttachment(attachments.length - 1);
        return;
      }
      const col = Math.min(cursorCol, currentLine.length);
      if (col > 0) {
        const newLines = [...lines];
        newLines[cursorLine] = currentLine.slice(0, col - 1) + currentLine.slice(col);
        setLines(newLines);
        setCursorCol(col - 1);
      } else if (cursorLine > 0) {
        const newLines = [...lines];
        const prevLen = newLines[cursorLine - 1].length;
        newLines[cursorLine - 1] += currentLine;
        newLines.splice(cursorLine, 1);
        setLines(newLines);
        setCursorLine(cursorLine - 1);
        setCursorCol(prevLen);
      }
      setShowComplete(false);
      return;
    }

    // Ctrl+G — внешний редактор
    if (key.ctrl && input === 'g') {
      const edited = openInEditor(text);
      if (edited !== null) setText(edited);
      return;
    }

    // Ctrl+V — вставка изображения или текста из clipboard
    if ((key.ctrl && (input === 'v' || input === '\x16')) || (key.meta && input === 'v')) {
      if (onImagePaste) {
        const imgPath = pasteImageFromClipboard();
        if (imgPath) {
          onImagePaste(imgPath);
          return;
        }
      }
      // Fallback: вставить текст из clipboard
      try {
        const clipText = execSync('pbpaste 2>/dev/null', { encoding: 'utf-8', timeout: 2000 }).trim();
        if (clipText) {
          const col = Math.min(cursorCol, currentLine.length);
          const newLines = [...lines];
          newLines[cursorLine] = currentLine.slice(0, col) + clipText + currentLine.slice(col);
          setLines(newLines);
          setCursorCol(col + clipText.length);
        }
      } catch { /* clipboard empty or inaccessible */ }
      return;
    }

    // Ctrl+U — clear line
    if (key.ctrl && input === 'u') {
      const newLines = [...lines];
      newLines[cursorLine] = '';
      setLines(newLines);
      return;
    }

    // Ctrl+K — delete to end of line
    if (key.ctrl && input === 'k') {
      const newLines = [...lines];
      newLines[cursorLine] = '';
      setLines(newLines);
      return;
    }

    // Ctrl+W — delete word
    if (key.ctrl && input === 'w') {
      const newLines = [...lines];
      newLines[cursorLine] = currentLine.replace(/\S+\s*$/, '');
      setLines(newLines);
      setCursorCol(newLines[cursorLine].length);
      return;
    }

    // Left/Right arrows — перемещение курсора
    if (key.leftArrow) {
      if (cursorCol > 0) {
        setCursorCol(cursorCol - 1);
      } else if (cursorLine > 0) {
        setCursorLine(cursorLine - 1);
        setCursorCol((lines[cursorLine - 1] ?? '').length);
      }
      return;
    }
    if (key.rightArrow) {
      if (cursorCol < currentLine.length) {
        setCursorCol(cursorCol + 1);
      } else if (cursorLine < lines.length - 1) {
        setCursorLine(cursorLine + 1);
        setCursorCol(0);
      }
      return;
    }
    // Home/End — начало/конец строки
    if (key.ctrl && input === 'a') { setCursorCol(0); return; }
    if (key.ctrl && input === 'e') { setCursorCol(currentLine.length); return; }

    // Regular character input — вставка в cursorCol
    if (input && !key.ctrl && !key.meta && input !== '\r' && input !== '\n') {
      const newLines = [...lines];
      const line = newLines[cursorLine] ?? '';
      const col = Math.min(cursorCol, line.length);
      newLines[cursorLine] = line.slice(0, col) + input + line.slice(col);
      setLines(newLines);
      setCursorCol(col + 1);
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
      {/* Attachment chips */}
      {attachments.length > 0 && (
        <Box paddingX={1} flexWrap="wrap">
          {attachments.map((p, i) => (
            <Box key={i} marginRight={1}>
              <Text color="cyan">[Image #{i + 1}</Text>
              <Text dimColor> {p.split('/').pop()}</Text>
              <Text color="red"> ✕</Text>
              <Text color="cyan">]</Text>
            </Box>
          ))}
          <Text dimColor> Backspace — удалить</Text>
        </Box>
      )}
      <Box paddingX={1}>
        {recording ? (
          <Box>
            <Text color="red" bold>{REC_FRAMES[recFrame]} </Text>
            <Text color="red">{WAVE_FRAMES[recFrame % WAVE_FRAMES.length]} </Text>
            <Text color="red">{voiceText || 'слушаю…'}</Text>
            <Text color="red"> ▌</Text>
            <Text dimColor>  {recSeconds}с · [пробел — стоп]</Text>
          </Box>
        ) : text ? (
          <Box>
            <Text color="magenta" bold>› </Text>
            <Box flexDirection="column">
              {lines.map((line, i) => {
                const col = i === cursorLine ? Math.min(cursorCol, line.length) : line.length;
                return (
                  <Text key={i}>
                    {line.slice(0, col)}
                    {i === cursorLine && <Text color="magenta">▌</Text>}
                    {line.slice(col)}
                  </Text>
                );
              })}
            </Box>
          </Box>
        ) : (
          <Box>
            <Text color="magenta" bold>› </Text>
            <Text dimColor>{placeholder ?? 'сообщение… (зажми пробел — голос, Ctrl+V — фото, ! shell)'}</Text>
          </Box>
        )}
      </Box>
    </Box>
  );
}