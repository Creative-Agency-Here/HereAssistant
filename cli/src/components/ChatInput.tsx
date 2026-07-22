import React, { useState, useRef, useEffect } from 'react';
import { Box, Text, useInput } from 'ink';
import { pasteImageFromClipboard } from '../clipboard.js';
import { openInEditor } from '../editor.js';
import { voiceRealtime, canRealtimeVoice } from '../voice.js';
import fs from 'node:fs';
import path from 'node:path';

const REC_FRAMES = ['●', '◉', '◎', '◉'];
const WAVE_FRAMES = ['▁▃▅▇', '▃▅▇▅', '▅▇▅▃', '▇▅▃▁', '▅▃▁▃', '▃▁▃▅'];

const SLASH_COMMANDS = [
  '/help', '/model', '/account', '/status', '/resume', '/rename', '/fork', '/search', '/bg',
  '/theme', '/archive', '/delete', '/mcp', '/plain', '/image', '/diff', '/new', '/compact', '/exit',
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

    // Пробел = hold-to-record (как в Claude Code)
    // Зажал пробел → через 400ms повторов старт записи
    // Отпустил (нет повторов 300ms) → стоп записи
    // Работает с любым текстом (не только пустым)
    if (input === ' ' && !key.ctrl && !key.meta && !key.shift) {
      if (recording) {
        // Ещё один пробел при записи = держит живым (key repeat)
        if (spaceHoldRef.current) clearTimeout(spaceHoldRef.current);
        spaceHoldRef.current = setTimeout(() => {
          // 300ms без повторов = отпустил пробел → стоп
          voiceRef.current?.kill();
          voiceRef.current = null;
          setRecording(false);
          // Дописываем распознанное к существующему тексту
          if (voiceText.trim()) {
            const current = text;
            const sep = current && !current.endsWith(' ') ? ' ' : '';
            setText(current + sep + voiceText.trim());
            setVoiceText('');
          }
        }, 300);
        return;
      }
      // Первый пробел — ждём повторы (hold detection)
      if (!spaceHoldRef.current && canRealtimeVoice()) {
        spaceHoldRef.current = setTimeout(() => {
          // 400ms повторов = зажал → старт записи
          setRecording(true);
          setVoiceText('');
          voiceRef.current = voiceRealtime(120, (partial) => {
            setVoiceText(partial);
          }, (final) => {
            setVoiceText(final);
          });
          // Таймер для детекта отпускания
          spaceHoldRef.current = setTimeout(() => {
            voiceRef.current?.kill();
            voiceRef.current = null;
            setRecording(false);
            if (voiceText.trim()) {
              const current = text;
              const sep = current && !current.endsWith(' ') ? ' ' : '';
              setText(current + sep + voiceText.trim());
              setVoiceText('');
            }
          }, 300);
        }, 400);
        return;
      }
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
      if (currentLine.length > 0) {
        const newLines = [...lines];
        newLines[cursorLine] = currentLine.slice(0, -1);
        setLines(newLines);
      } else if (cursorLine > 0) {
        const newLines = [...lines];
        newLines.splice(cursorLine, 1);
        setLines(newLines);
        setCursorLine(cursorLine - 1);
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

    // Ctrl+V — вставка изображения из clipboard (как в Claude Code)
    if (key.ctrl && input === 'v') {
      if (onImagePaste) {
        const imgPath = pasteImageFromClipboard();
        if (imgPath) {
          onImagePaste(imgPath);
        }
      }
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
      return;
    }

    // Regular character input
    if (input && !key.ctrl && !key.meta && input !== '\r' && input !== '\n') {
      const newLines = [...lines];
      newLines[cursorLine] = currentLine + input;
      setLines(newLines);
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
              {lines.map((line, i) => (
                <Text key={i}>
                  {line}
                  {i === cursorLine && <Text color="magenta">▌</Text>}
                </Text>
              ))}
            </Box>
          </Box>
        ) : (
          <Box>
            <Text color="magenta" bold>› </Text>
            <Text dimColor>{placeholder ?? 'сообщение… (пробел — голос, Ctrl+V — фото, ! shell)'}</Text>
          </Box>
        )}
      </Box>
    </Box>
  );
}