import React, { useEffect, useState } from 'react';
import { EventEmitter } from 'node:events';
import { Box, Text, useInput } from 'ink';
import type { HaSession } from '../ha-sessions.js';
import { haSessionTitle } from '../ha-sessions.js';
import { formatSessionAge } from '../sessions.js';
import type { ParsedEscapeKey, ParsedNavigationKey } from '../mouse-filter.js';

const VISIBLE = 12;
const PREVIEW_LINES = 20;

interface Props {
  sessions: HaSession[];
  onSelect: (id: string) => void;
  onCancel: () => void;
}

export function SessionPicker({ sessions, onSelect, onCancel }: Props) {
  const [cursor, setCursor] = useState(0);
  const [preview, setPreview] = useState<HaSession | null>(null);

  useEffect(() => {
    const keysEmitter = (globalThis as Record<string, unknown>).__ha_keys as EventEmitter | undefined;
    if (!keysEmitter || preview) return;
    const onArrow = ({ direction }: ParsedNavigationKey) => {
      if (direction === 'up') setCursor((current) => Math.max(0, current - 1));
      if (direction === 'down') setCursor((current) => Math.min(sessions.length - 1, current + 1));
      if (direction === 'home') setCursor(0);
      if (direction === 'end') setCursor(Math.max(0, sessions.length - 1));
    };
    keysEmitter.on('arrow-key', onArrow);
    return () => { keysEmitter.off('arrow-key', onArrow); };
  }, [preview, sessions.length]);

  useEffect(() => {
    const keysEmitter = (globalThis as Record<string, unknown>).__ha_keys as EventEmitter | undefined;
    if (!keysEmitter) return;
    const onEscape = ({ modifiers = 1 }: ParsedEscapeKey = { modifiers: 1 }) => {
      const hasShift = ((modifiers - 1) & 1) !== 0;
      if (hasShift) return;
      if (preview) setPreview(null);
      else onCancel();
    };
    keysEmitter.on('escape-key', onEscape);
    return () => { keysEmitter.off('escape-key', onEscape); };
  }, [preview, onCancel]);

  useInput((input, key) => {
    if (preview) {
      // Режим превью: Enter — войти, ESC — назад к списку
      if (key.return) {
        onSelect(preview.id);
      } else if (key.escape || input === 'q') {
        setPreview(null);
      }
      return;
    }
    // Режим списка
    if (key.upArrow) {
      setCursor((c) => Math.max(0, c - 1));
    } else if (key.downArrow) {
      setCursor((c) => Math.min(sessions.length - 1, c + 1));
    } else if (key.return) {
      if (sessions[cursor]) setPreview(sessions[cursor]);
    } else if (key.escape || input === 'q') {
      onCancel();
    }
  }, { isActive: true });

  // ── Режим превью ──
  if (preview) {
    const title = haSessionTitle(preview);
    const msgs = preview.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
    const recent = msgs.slice(-PREVIEW_LINES);
    const lastProvider = [...preview.messages].reverse().find((m) => m.provider)?.provider;

    return (
      <Box flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1} paddingY={0}>
        <Box>
          <Text bold color="yellow"> {title.length > 50 ? title.slice(0, 47) + '…' : title} </Text>
        </Box>
        <Text dimColor>
          {formatSessionAge(preview.updatedAt)} · {msgs.length} сообщ.
          {lastProvider ? ` · ${lastProvider}` : ''} · {preview.id.slice(0, 16)}
        </Text>
        <Text dimColor>{'─'.repeat(50)}</Text>
        {recent.map((m, i) => {
          const isUser = m.role === 'user';
          const lines = m.text.split('\n');
          const truncated = lines.length > 3;
          const shown = lines.slice(0, 3).join('\n');
          return (
            <Box key={i} flexDirection="column" marginBottom={0}>
              <Text>
                <Text color={isUser ? 'cyan' : 'green'} bold>{isUser ? '› ' : '◂ '}</Text>
                <Text color={isUser ? 'cyan' : undefined}>{shown.length > 200 ? shown.slice(0, 197) + '…' : shown}</Text>
                {truncated && <Text dimColor> …</Text>}
              </Text>
            </Box>
          );
        })}
        {msgs.length > PREVIEW_LINES && (
          <Text dimColor>… ещё {msgs.length - PREVIEW_LINES} сообщений выше</Text>
        )}
        <Text dimColor>{'─'.repeat(50)}</Text>
        <Text>
          <Text color="yellow" bold>Enter</Text><Text dimColor> войти в сессию · </Text>
          <Text color="yellow" bold>ESC</Text><Text dimColor> назад к списку</Text>
        </Text>
      </Box>
    );
  }

  // ── Режим списка ──
  const start = Math.max(0, Math.min(cursor - Math.floor(VISIBLE / 2), sessions.length - VISIBLE));
  const visible = sessions.slice(start, start + VISIBLE);

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} paddingY={0}>
      <Box>
        <Text bold color="cyan"> Сессии HA </Text>
        <Text dimColor>↑↓ выбор · Enter превью · ESC отмена</Text>
      </Box>
      <Text> </Text>
      {visible.map((s, i) => {
        const idx = start + i;
        const active = idx === cursor;
        const title = haSessionTitle(s);
        const msgCount = s.messages.filter((m) => m.role === 'user' || m.role === 'assistant').length;
        const lastProvider = [...s.messages].reverse().find((m) => m.provider)?.provider;
        return (
          <Box key={s.id}>
            <Text color={active ? 'cyan' : undefined} bold={active} inverse={active}>
              {active ? ' ❯ ' : '   '}
            </Text>
            <Box flexDirection="column">
              <Text color={active ? 'cyan' : undefined} bold={active}>
                {title.length > 60 ? title.slice(0, 57) + '…' : title}
              </Text>
              <Text dimColor>
                {formatSessionAge(s.updatedAt)} · {msgCount} сообщ. · {s.id.slice(0, 16)}
                {lastProvider ? ` · ${lastProvider}` : ''}
              </Text>
            </Box>
          </Box>
        );
      })}
      {sessions.length > VISIBLE && (
        <Text dimColor> … ещё {sessions.length - VISIBLE} (↑↓ листать)</Text>
      )}
    </Box>
  );
}
