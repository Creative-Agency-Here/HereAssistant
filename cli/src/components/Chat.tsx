import React, { useState, useRef, useCallback } from 'react';
import { Box, Text, useApp, useInput } from 'ink';
import TextInput from 'ink-text-input';
import Spinner from 'ink-spinner';
import type { Account, ChatMessage, StreamEvent, ToolCall } from '../types.js';
import { makeProvider } from '../providers/index.js';
import { ToolCallBlock } from './ToolCallBlock.js';
import { renderMarkdown } from './markdown.js';

function makeId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function Chat({ account, cwd }: { account: Account; cwd: string }) {
  const { exit } = useApp();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [model] = useState(account.default_model || '');
  const sessionIdRef = useRef<string | null>(null);

  const addMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLastAssistant = useCallback((updater: (msg: ChatMessage) => ChatMessage) => {
    setMessages((prev) => {
      const idx = prev.length - 1;
      if (idx < 0 || prev[idx].role !== 'assistant') return prev;
      const next = [...prev];
      next[idx] = updater(next[idx]);
      return next;
    });
  }, []);

  const handleSubmit = useCallback(async (value: string) => {
    const text = value.trim();
    if (!text) return;
    setInput('');

    if (text === '/exit' || text === '/quit') { exit(); return; }
    if (text === '/new') {
      sessionIdRef.current = null;
      addMessage({ id: makeId(), role: 'system', text: 'Новая сессия — контекст очищен.', toolCalls: [], timestamp: Date.now(), streaming: false });
      return;
    }

    addMessage({ id: makeId(), role: 'user', text, toolCalls: [], timestamp: Date.now(), streaming: false });

    const assistantMsg: ChatMessage = {
      id: makeId(), role: 'assistant', text: '', toolCalls: [], timestamp: Date.now(), streaming: true,
    };
    addMessage(assistantMsg);
    setBusy(true);

    try {
      const provider = makeProvider(account);
      const result = await provider.run(text, cwd, sessionIdRef.current, model || null, (event: StreamEvent) => {
        if (event.type === 'text' && typeof event.text === 'string') {
          updateLastAssistant((m) => ({ ...m, text: m.text + (event.text as string) }));
        } else if (event.type === 'tool_start' && event.tool) {
          const tool = event.tool as ToolCall;
          updateLastAssistant((m) => ({ ...m, toolCalls: [...m.toolCalls, { ...tool }] }));
        } else if (event.type === 'tool_end') {
          const toolId = String(event.toolId ?? '');
          const output = event.output != null ? String(event.output) : '';
          const isError = Boolean(event.isError);
          updateLastAssistant((m) => ({
            ...m,
            toolCalls: m.toolCalls.map((t) =>
              t.id === toolId ? { ...t, status: isError ? 'error' as const : 'done' as const, output } : t,
            ),
          }));
        }
      });

      if (result.sessionId) sessionIdRef.current = result.sessionId;
      updateLastAssistant((m) => ({ ...m, text: result.text || m.text, streaming: false }));
    } catch (err) {
      updateLastAssistant((m) => ({
        ...m,
        text: `✗ Ошибка: ${err instanceof Error ? err.message : String(err)}`,
        streaming: false,
      }));
    } finally {
      setBusy(false);
    }
  }, [account, cwd, model, addMessage, updateLastAssistant, exit]);

  useInput((input, key) => {
    if (key.ctrl && input === 'c') exit();
  });

  return (
    <Box flexDirection="column" height="100%">
      <Box borderStyle="single" borderBottom={false} borderLeft={false} borderRight={false} paddingX={1}>
        <Text bold color="magenta">HereAssistant</Text>
        <Text dimColor> · {account.label}</Text>
        <Text dimColor> · {model || 'default'}</Text>
        <Text dimColor> · {cwd.split('/').pop()}</Text>
        {sessionIdRef.current && <Text dimColor> · сессия {sessionIdRef.current.slice(0, 8)}</Text>}
      </Box>

      <Box flexDirection="column" flexGrow={1} overflow="hidden">
        {messages.map((msg) => (
          <Box key={msg.id} flexDirection="column" marginBottom={1}>
            {msg.role === 'user' && (
              <Box>
                <Text color="cyan" bold>› </Text>
                <Text>{msg.text}</Text>
              </Box>
            )}
            {msg.role === 'system' && (
              <Box><Text dimColor italic>{msg.text}</Text></Box>
            )}
            {msg.role === 'assistant' && (
              <Box flexDirection="column">
                {msg.toolCalls.map((tool, i) => (
                  <ToolCallBlock key={tool.id} tool={tool} index={i} />
                ))}
                {msg.text ? (
                  <Box marginTop={msg.toolCalls.length > 0 ? 1 : 0} flexDirection="column">
                    {renderMarkdown(msg.text).map((line, i) => (
                      <Text key={i}>{line}</Text>
                    ))}
                    {msg.streaming && <Text color="yellow"> ▌</Text>}
                  </Box>
                ) : (
                  msg.streaming && msg.toolCalls.length === 0 && (
                    <Box><Text color="yellow"><Spinner type="dots" /> думаю…</Text></Box>
                  )
                )}
              </Box>
            )}
          </Box>
        ))}
      </Box>

      <Box borderTop borderStyle="single" paddingX={1}>
        {busy ? (
          <Text dimColor><Spinner type="dots" /> агент работает…</Text>
        ) : (
          <Box>
            <Text color="magenta" bold>› </Text>
            <TextInput
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
              placeholder="сообщение… (/new /exit)"
            />
          </Box>
        )}
      </Box>
    </Box>
  );
}