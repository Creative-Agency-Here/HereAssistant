import React, { useState, useRef, useCallback } from 'react';
import { Box, Text, useApp, useInput } from 'ink';
import Spinner from 'ink-spinner';
import type { Account, ChatMessage, StreamEvent, ToolCall } from '../types.js';
import { makeProvider } from '../providers/index.js';
import { ToolCallBlock } from './ToolCallBlock.js';
import { ChatInput } from './ChatInput.js';
import { StatusBar } from './StatusBar.js';
import { RunSummary } from './RunSummary.js';
import { renderMarkdown } from './markdown.js';
import { handleCommand, type CommandContext } from '../commands.js';
import { startWorkingTitle, setIdleTitle, stopWorkingTitle } from '../terminal-title.js';
import { cleanClipboardCache } from '../clipboard.js';
import { loadConfig } from '../config.js';
import { memoryPrompt } from '../memory.js';
import { getTheme, type Theme } from '../themes.js';
import { renderInlineImage, supportsInlineImages } from '../terminal-images.js';
import { execSync, spawn } from 'node:child_process';

function makeId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function Chat({ account: initialAccount, cwd }: { account: Account; cwd: string }) {
  const { exit } = useApp();
  const config = React.useMemo(() => loadConfig(cwd), [cwd]);
  const memory = React.useMemo(() => memoryPrompt(cwd), [cwd]);
  const [themeName, setThemeName] = useState(config.theme || 'dark');
  const theme = React.useMemo(() => getTheme(themeName), [themeName]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [account, setAccount] = useState(initialAccount);
  const [model, setModel] = useState(config.defaultModel || initialAccount.default_model || '');
  const [tokensIn, setTokensIn] = useState(0);
  const [tokensOut, setTokensOut] = useState(0);
  const [lastDuration, setLastDuration] = useState(0);
  const [lastTokensIn, setLastTokensIn] = useState(0);
  const [lastTokensOut, setLastTokensOut] = useState(0);
  const [thinking, setThinking] = useState('');
  const [attachments, setAttachments] = useState<string[]>([]);
  const [sessionName, setSessionName] = useState<string | null>(null);
  const [promptCount, setPromptCount] = useState(0);
  const sessionIdRef = useRef<string | null>(null);
  const project = cwd.split('/').pop() ?? cwd;

  // Очистка кеша clipboard при старте
  React.useEffect(() => { cleanClipboardCache(); }, []);

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

  const doExit = useCallback(() => {
    stopWorkingTitle();
    exit();
  }, [exit]);

  const handleSubmit = useCallback(async (value: string) => {
    const text = value.trim();
    if (!text) return;

    // Slash commands
    if (text.startsWith('/')) {
      const ctx: CommandContext = {
        account, model, sessionId: sessionIdRef.current, cwd,
        tokensIn, tokensOut,
        setModel: (m) => setModel(m),
        setAccount: (a) => { setAccount(a); setModel(a.default_model || ''); },
        resetSession: () => { sessionIdRef.current = null; },
        setSessionId: (id) => { sessionIdRef.current = id; },
        renameSession: (name) => {
          setSessionName(name);
          setIdleTitle(name, promptCount);
        },
        setTheme: (name) => { setThemeName(name); },
        forkSession: () => { sessionIdRef.current = `fork-${makeId()}`; },
        backgroundPrompt: (prompt) => {
          const child = spawn('node', [
            new URL('../index.js', import.meta.url).pathname,
            '-a', account.label, '--resume', sessionIdRef.current || '',
          ], { cwd, detached: true, stdio: 'ignore', env: { ...process.env, HA_BG_PROMPT: prompt } });
          child.unref();
          addMessage({ id: makeId(), role: 'system', text: `🔄 фоновый агент запущен (PID ${child.pid})`, toolCalls: [], timestamp: Date.now(), streaming: false });
        },
        voiceInput: (text) => {
          // Голосовой текст вставляется как пользовательское сообщение и отправляется
          handleSubmit(text);
        },
        print: (t) => addMessage({ id: makeId(), role: 'system', text: t, toolCalls: [], timestamp: Date.now(), streaming: false }),
        exit: doExit,
        attachImage: (p) => setAttachments((prev) => [...prev, p]),
      };
      if (handleCommand(text, ctx)) return;
    }

    addMessage({ id: makeId(), role: 'user', text, toolCalls: [], timestamp: Date.now(), streaming: false });
    const assistantMsg: ChatMessage = {
      id: makeId(), role: 'assistant', text: '', toolCalls: [], timestamp: Date.now(), streaming: true,
    };
    addMessage(assistantMsg);
    setBusy(true);
    setThinking('');
    setLastDuration(0);
    setLastTokensIn(0);
    setLastTokensOut(0);
    setPromptCount((c) => c + 1);
    startWorkingTitle(sessionName || project, promptCount + 1);
    const t0 = Date.now();

    try {
      const provider = makeProvider(account);
      const currentAttachments = [...attachments];
      setAttachments([]);
      const fullPrompt = memory ? `${text}${memory}` : text;
      const result = await provider.run(fullPrompt, cwd, sessionIdRef.current, model || null, (event: StreamEvent) => {
        if (event.type === 'text' && typeof event.text === 'string') {
          updateLastAssistant((m) => ({ ...m, text: m.text + (event.text as string) }));
        } else if (event.type === 'thinking' && typeof event.text === 'string') {
          setThinking((prev) => prev + (event.text as string));
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
      }, currentAttachments);

      const duration = Date.now() - t0;
      if (result.sessionId) sessionIdRef.current = result.sessionId;
      if (result.tokensIn) { setTokensIn((p) => p + result.tokensIn!); setLastTokensIn(result.tokensIn); }
      if (result.tokensOut) { setTokensOut((p) => p + result.tokensOut!); setLastTokensOut(result.tokensOut); }
      setLastDuration(duration);
      updateLastAssistant((m) => ({ ...m, text: result.text || m.text, streaming: false }));
    } catch (err) {
      setLastDuration(Date.now() - t0);
      updateLastAssistant((m) => ({
        ...m,
        text: `✗ Ошибка: ${err instanceof Error ? err.message : String(err)}`,
        streaming: false,
      }));
    } finally {
      setBusy(false);
      setThinking('');
      setIdleTitle(sessionName || project, promptCount);
    }
  }, [account, cwd, model, tokensIn, tokensOut, project, addMessage, updateLastAssistant, doExit]);

  const handleShellCommand = useCallback((cmd: string) => {
    addMessage({ id: makeId(), role: 'user', text: `! ${cmd}`, toolCalls: [], timestamp: Date.now(), streaming: false });
    try {
      const output = execSync(cmd, { cwd, encoding: 'utf-8', timeout: 30000, shell: '/bin/bash' });
      addMessage({ id: makeId(), role: 'system', text: output.trim() || '(пусто)', toolCalls: [], timestamp: Date.now(), streaming: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      addMessage({ id: makeId(), role: 'system', text: `✗ ${msg.slice(0, 500)}`, toolCalls: [], timestamp: Date.now(), streaming: false });
    }
  }, [cwd, addMessage]);

  useInput((input, key) => {
    if (key.ctrl && input === 'c') doExit();
  });

  return (
    <Box flexDirection="column" height="100%">
      <StatusBar
        account={account.label}
        model={model || account.default_model || 'default'}
        sessionId={sessionIdRef.current}
        sessionName={sessionName}
        tokensIn={tokensIn}
        tokensOut={tokensOut}
        cwd={cwd}
        provider={account.provider}
        taskCount={promptCount}
        busy={busy}
      />

      <Box flexDirection="column" flexGrow={1} overflow="hidden">
        {messages.map((msg) => (
          <Box key={msg.id} flexDirection="column" marginBottom={1}>
            {msg.role === 'user' && (
              <Box><Text color="cyan" bold>› </Text><Text>{msg.text}</Text></Box>
            )}
            {msg.role === 'system' && (
              <Box flexDirection="column">
                {msg.text.split('\n').map((line, i) => (
                  <Text key={i} dimColor>{line}</Text>
                ))}
              </Box>
            )}
            {msg.role === 'assistant' && (
              <Box flexDirection="column">
                {msg.toolCalls.map((tool) => (
                  <ToolCallBlock key={tool.id} tool={tool} />
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
                {!msg.streaming && msg.role === 'assistant' && lastDuration > 0 && (
                  <RunSummary durationMs={lastDuration} tokensIn={lastTokensIn} tokensOut={lastTokensOut} />
                )}
              </Box>
            )}
          </Box>
        ))}
        {thinking && (
          <Box marginLeft={1}>
            <Text dimColor italic>💭 {thinking.slice(-200)}</Text>
          </Box>
        )}
      </Box>

      <Box borderTop borderStyle="single" flexDirection="column">
        {attachments.length > 0 && (
          <Box paddingX={1} flexDirection="column">
            {attachments.map((p) => (
              <Text key={p} dimColor>📎 {p.split('/').pop()}</Text>
            ))}
            {supportsInlineImages() && attachments.map((p) => (
              <Text key={`img-${p}`}>{renderInlineImage(p)}</Text>
            ))}
          </Box>
        )}
        <ChatInput
          onSubmit={handleSubmit}
          onImagePaste={(p) => setAttachments((prev) => [...prev, p])}
          onShellCommand={handleShellCommand}
          disabled={busy}
          cwd={cwd}
        />
      </Box>
    </Box>
  );
}