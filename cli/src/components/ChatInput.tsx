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
  const holdRef = useRef({ count: 0, lastTime: 0, recLastSpace: 0 });
  const recordingRef = useRef(false);

  // Анимация записи + sync ref
  useEffect(() => {
    recordingRef.current = recording;
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

    // Пробел = печатает пробел + hold detection через refs (без stale closure)
    if (input === ' ' && !key.ctrl && !key.meta && !key.shift) {
      const h = holdRef.current;
      const now = Date.now();

      if (recordingRef.current) {
        // Во время записи: repeat игнорим, стоп по одиночному нажатию
        if (now - h.recLastSpace > 300) {
          // Одиночное = стоп
          voiceRef.current?.kill();
          voiceRef.current = null;
          setRecording(false);
          h.recLastSpace = 0;
          // Дописываем voiceText в позицию курсора
          setVoiceText((vt) => {
            if (vt.trim()) {
              setLines((prev) => {
                const newLines = [...prev];
                const line = newLines[cursorLine] ?? '';
                const col = Math.min(cursorCol, line.length);
                const insert = (col > 0 && line[col - 1] !== ' ' ? ' ' : '') + vt.trim();
                newLines[cursorLine] = line.slice(0, col) + insert + line.slice(col);
                setCursorCol(col + insert.length);
                return newLines;
              });
            }
            return '';
          });
        } else {
          h.recLastSpace = now; // repeat — игнорим
        }
        return;
      }

      // Печатаем пробел через functional update (видит актуальный state)
      setLines((prev) => {
        const newLines = [...prev];
        const line = newLines[cursorLine] ?? '';
        const col = Math.min(cursorCol, line.length);
        newLines[cursorLine] = line.slice(0, col) + ' ' + line.slice(col);
        setCursorCol(col + 1);

        // Hold detection
        if (now - h.lastTime < 150) {
          h.count++;
        } else {
          h.count = 1;
        }
        h.lastTime = now;

        if (h.count >= 4 && canRealtimeVoice()) {
          // Зажал → убираем пробелы hold из ТЕКУЩЕЙ newLines
          const removeEnd = col + 1;
          const removeStart = Math.max(0, removeEnd - h.count);
          newLines[cursorLine] = newLines[cursorLine].slice(0, removeStart) + newLines[cursorLine].slice(removeEnd);
          setCursorCol(removeStart);
          h.count = 0;
          h.recLastSpace = 0;
          setRecording(true);
          setVoiceText('');
          voiceRef.current = voiceRealtime(120, (p) => setVoiceText(p), (f) => setVoiceText(f));
        }
        return newLines;
      });
      // Сброс счётчика
      setTimeout(() => { h.count = 0; }, 300);
      return;
    }

    // Ctrl+M = toggle голос (альтернатива)
    if (key.ctrl && input === 'm') {
      if (recordingRef.current) {
        voiceRef.current?.kill();
        voiceRef.current = null;
        setRecording(false);
        if (voiceText.trim()) {
          const col = Math.min(cursorCol, currentLine.length);
          const newLines = [...lines];
          const insert = (col > 0 && currentLine[col - 1] !== ' ' ? ' ' : '') + voiceText.trim();
          newLines[cursorLine] = currentLine.slice(0, col) + insert + currentLine.slice(col);
          setLines(newLines);
          setCursorCol(col + insert.length);
          setVoiceText('');
        }
      } else if (canRealtimeVoice()) {
        setRecording(true);
        setVoiceText('');
        voiceRef.current = voiceRealtime(120, (p) => setVoiceText(p), (f) => setVoiceText(f));
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
        // Проверяем: курсор после [Image #N] — удаляем тег целиком
        const before = currentLine.slice(0, col);
        const tagMatch = before.match(/\[Image #\d+\]\s?$/);
        const newLines = [...lines];
        if (tagMatch) {
          const removeLen = tagMatch[0].length;
          newLines[cursorLine] = currentLine.slice(0, col - removeLen) + currentLine.slice(col);
          setCursorCol(col - removeLen);
          // Удаляем соответствующий аттачмент
          if (onRemoveAttachment && attachments.length > 0) {
            onRemoveAttachment(attachments.length - 1);
          }
        } else {
          newLines[cursorLine] = currentLine.slice(0, col - 1) + currentLine.slice(col);
          setCursorCol(col - 1);
        }
        setLines(newLines);
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
          setCursorCol(col + sep.length + tag.length + 1);
          onImagePaste(imgPath);
          return;
        }
      }
      return;
    }

    // Ctrl+V / Cmd+V — только текст из clipboard (изображения через Ctrl+I)
    if ((key.ctrl || key.meta) && (input === 'v' || input === '\x16')) {
      try {
        const clipText = execSync('pbpaste 2>/dev/null', { encoding: 'utf-8', timeout: 2000 }).trim();
        if (clipText) {
          const col = Math.min(cursorCol, currentLine.length);
          const newLines = [...lines];
          newLines[cursorLine] = currentLine.slice(0, col) + clipText + currentLine.slice(col);
          setLines(newLines);
          setCursorCol(col + clipText.length);
        }
      } catch { /* empty */ }
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
        ) : text ? (
          <Box>
            <Text color="magenta" bold>› </Text>
            <Box flexDirection="column">
              {lines.map((line, i) => {
                const col = i === cursorLine ? Math.min(cursorCol, line.length) : line.length;
                const before = line.slice(0, col);
                const after = line.slice(col);
                // Подсветка [Image #N] синим
                const renderWithTags = (s: string) => {
                  const parts = s.split(/(\[Image #\d+\])/g);
                  return parts.map((p, j) =>
                    p.startsWith('[Image') ? <Text key={j} color="cyan" bold>{p}</Text> : <Text key={j}>{p}</Text>
                  );
                };
                return (
                  <Text key={i}>
                    {renderWithTags(before)}
                    {i === cursorLine && <Text color="magenta">▌</Text>}
                    {renderWithTags(after)}
                  </Text>
                );
              })}
            </Box>
          </Box>
        ) : (
          <Box>
            <Text color="magenta" bold>› </Text>
            <Text dimColor>{placeholder ?? 'сообщение… (зажми пробел — голос, Ctrl+I — фото, ! shell)'}</Text>
          </Box>
        )}
      </Box>
    </Box>
  );
}