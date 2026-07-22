import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Box, Text, useApp, useInput, useStdout } from 'ink';
import type { Account, ChatMessage, StreamEvent, ToolCall } from '../types.js';
import { makeProvider } from '../providers/index.js';
import { ChatInput } from './ChatInput.js';
import { StatusBar } from './StatusBar.js';
import { RunSummary } from './RunSummary.js';
import { renderMarkdown } from './markdown.js';
import { handleCommand, type CommandContext } from '../commands.js';
import { startWorkingTitle, setIdleTitle, stopWorkingTitle } from '../terminal-title.js';
import { cleanClipboardCache } from '../clipboard.js';
import { loadConfig } from '../config.js';
import { memoryPrompt } from '../memory.js';
import { getTheme } from '../themes.js';
import { renderInlineImage, supportsInlineImages } from '../terminal-images.js';
import { useFullscreen } from '../hooks/useFullscreen.js';
import { useMouse, type MouseEvent } from '../hooks/useMouse.js';
import { execSync, spawn } from 'node:child_process';
import { writeIntegrationState } from '../integration-state.js';

function makeId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** Хранит маппинг row → toolId для hit-testing кликов. */
interface LayoutEntry {
  row: number;
  toolId: string;
  messageIdx: number;
}

export function FullscreenChat({ account: initialAccount, cwd, integrationId }: { account: Account; cwd: string; integrationId?: string }) {
  const { exit } = useApp();
  const { stdout } = useStdout();
  const termRows = stdout?.rows || process.stdout.rows || 24;
  const termCols = stdout?.columns || process.stdout.columns || 80;

  useFullscreen(true);

  const config = React.useMemo(() => loadConfig(cwd), [cwd]);
  const memory = React.useMemo(() => memoryPrompt(cwd), [cwd]);
  const [themeName] = useState(config.theme || 'dark');
  const [plainMode, setPlainMode] = useState(config.plainMode || false);
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
  const [scrollOffset, setScrollOffset] = useState(0);
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [permMode, setPermMode] = useState(0); // index into PERM_MODES
  const sessionIdRef = useRef<string | null>(null);
  const project = cwd.split('/').pop() ?? cwd;
  const layoutRef = useRef<LayoutEntry[]>([]);

  const PERM_MODES = ['acceptEdits', 'auto', 'plan', 'default'] as const;
  const PERM_LABELS: Record<string, string> = {
    acceptEdits: 'edits✓', auto: 'auto', plan: 'read-only', default: 'ask',
  };

  React.useEffect(() => {
    cleanClipboardCache();
    if (integrationId) writeIntegrationState(integrationId, { state: 'open', cwd });
    return () => { if (integrationId) writeIntegrationState(integrationId, { state: 'closed', cwd }); };
  }, []);

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

  const toggleTool = useCallback((toolId: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(toolId)) next.delete(toolId);
      else next.add(toolId);
      return next;
    });
  }, []);

  // Mouse handler: клик по tool-блоку → toggle
  useMouse((event: MouseEvent) => {
    if (event.type === 'press' && event.button === 'left') {
      const hit = layoutRef.current.find((e) => e.row === event.row);
      if (hit) toggleTool(hit.toolId);
    }
    if (event.type === 'scroll') {
      setScrollOffset((prev) => Math.max(0, prev + (event.button === 'scroll-up' ? -3 : 3)));
    }
  });

  const handleSubmit = useCallback(async (value: string) => {
    const text = value.trim();
    if (!text) return;

    if (text.startsWith('/')) {
      const ctx: CommandContext = {
        account, model, sessionId: sessionIdRef.current, cwd,
        tokensIn, tokensOut,
        setModel: (m) => setModel(m),
        setAccount: (a) => { setAccount(a); setModel(a.default_model || ''); },
        resetSession: () => { sessionIdRef.current = null; },
        setSessionId: (id) => { sessionIdRef.current = id; },
        renameSession: (name) => { setSessionName(name); setIdleTitle(name, promptCount); },
        setTheme: () => {},
        forkSession: () => { sessionIdRef.current = `fork-${makeId()}`; },
        backgroundPrompt: (prompt) => {
          const child = spawn('node', [
            new URL('../index.js', import.meta.url).pathname,
            '-a', account.label,
          ], { cwd, detached: true, stdio: 'ignore', env: { ...process.env, HA_BG_PROMPT: prompt } });
          child.unref();
          addMessage({ id: makeId(), role: 'system', text: `🔄 фон: PID ${child.pid}`, toolCalls: [], timestamp: Date.now(), streaming: false });
        },
        voiceInput: (t) => { handleSubmit(t); },
        togglePlain: () => { setPlainMode((p) => !p); },
        copyLast: () => {
          const last = [...messages].reverse().find((m) => m.role === 'assistant' && m.text);
          if (last?.text) {
            try {
              execSync(`printf '%s' ${JSON.stringify(last.text)} | pbcopy`, { timeout: 3000 });
              addMessage({ id: makeId(), role: 'system', text: '📋 скопировано в clipboard', toolCalls: [], timestamp: Date.now(), streaming: false });
            } catch {
              addMessage({ id: makeId(), role: 'system', text: '✗ не удалось скопировать', toolCalls: [], timestamp: Date.now(), streaming: false });
            }
          } else {
            addMessage({ id: makeId(), role: 'system', text: '✗ нет ответа для копирования', toolCalls: [], timestamp: Date.now(), streaming: false });
          }
        },
        print: (t) => addMessage({ id: makeId(), role: 'system', text: t, toolCalls: [], timestamp: Date.now(), streaming: false }),
        exit: doExit,
        attachImage: (p) => setAttachments((prev) => [...prev, p]),
      };
      if (handleCommand(text, ctx)) return;
    }

    addMessage({ id: makeId(), role: 'user', text, toolCalls: [], timestamp: Date.now(), streaming: false, attachments: attachments.length > 0 ? [...attachments] : undefined });
    const assistantMsg: ChatMessage = {
      id: makeId(), role: 'assistant', text: '', toolCalls: [], timestamp: Date.now(), streaming: true,
    };
    addMessage(assistantMsg);
    setBusy(true);
    setThinking('');
    setLastDuration(0);
    setPromptCount((c) => c + 1);
    startWorkingTitle(sessionName || project, promptCount + 1);
    if (integrationId) writeIntegrationState(integrationId, { state: 'working', cwd, title: text, taskCount: promptCount + 1 });
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
        ...m, text: `✗ ${err instanceof Error ? err.message : String(err)}`, streaming: false,
      }));
    } finally {
      setBusy(false);
      setThinking('');
      setIdleTitle(sessionName || project, promptCount);
      if (integrationId) writeIntegrationState(integrationId, { state: 'open', cwd, taskCount: promptCount, sessionId: sessionIdRef.current });
    }
  }, [account, cwd, model, tokensIn, tokensOut, project, promptCount, sessionName, attachments, memory, addMessage, updateLastAssistant, doExit]);

  const handleShellCommand = useCallback((cmd: string) => {
    addMessage({ id: makeId(), role: 'user', text: `! ${cmd}`, toolCalls: [], timestamp: Date.now(), streaming: false });
    try {
      const output = execSync(cmd, { cwd, encoding: 'utf-8', timeout: 30000, shell: '/bin/bash' });
      addMessage({ id: makeId(), role: 'system', text: output.trim() || '(пусто)', toolCalls: [], timestamp: Date.now(), streaming: false });
    } catch (err) {
      addMessage({ id: makeId(), role: 'system', text: `✗ ${err instanceof Error ? err.message.slice(0, 500) : String(err)}`, toolCalls: [], timestamp: Date.now(), streaming: false });
    }
  }, [cwd, addMessage]);

  // Keyboard: scroll + permission mode
  useInput((input, key) => {
    if (key.ctrl && input === 'c') { doExit(); return; }
    if (key.pageUp) setScrollOffset((p) => Math.max(0, p - 10));
    if (key.pageDown) setScrollOffset((p) => p + 10);
    // Shift+Tab — cycle permission mode (как в Claude Code)
    if (key.tab && key.shift) {
      setPermMode((p) => (p + 1) % PERM_MODES.length);
    }
  });

  // Build layout map for mouse hit testing
  const TOOL_ICONS: Record<string, string> = {
    read_file: '📄', write_file: '✏️', edit: '✏️', run_shell_command: '⚡',
    grep_search: '🔍', glob: '📁', agent: '🤖',
  };

  // Calculate visible messages (simple: show last N that fit)
  const visibleAreaHeight = termRows - 5; // status(1) + pinned(1) + border(1) + input(1) + padding(1)
  const visibleMessages = messages.slice(Math.max(0, messages.length - visibleAreaHeight));

  // Последний пользовательский запрос (pinned вверху)
  const lastUserMsg = [...messages].reverse().find((m) => m.role === 'user');

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    setScrollOffset(0); // 0 = bottom
  }, [messages.length]);

  // Нумерация: считаем только user+assistant пары
  const msgNumbers = new Map<string, number>();
  let num = 0;
  for (const m of messages) {
    if (m.role === 'user') { num++; msgNumbers.set(m.id, num); }
    else if (m.role === 'assistant') { msgNumbers.set(m.id, num); }
  }

  return (
    <Box flexDirection="column" height={termRows}>
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
        permMode={PERM_LABELS[PERM_MODES[permMode]] || 'ask'}
      />

      {/* Floating scroll-хедер (как дата в Telegram) — только при скролле вверх */}
      {scrollOffset > 0 && lastUserMsg && (
        <Box paddingX={1} borderStyle="single" borderBottom={false} borderLeft={false} borderRight={false}>
          <Text dimColor>↑ #{msgNumbers.get(lastUserMsg.id) ?? '?'} </Text>
          <Text color="cyan">{lastUserMsg.text.length > termCols - 15 ? lastUserMsg.text.slice(0, termCols - 18) + '…' : lastUserMsg.text}</Text>
          <Text dimColor>  · скролл</Text>
        </Box>
      )}

      <Box flexDirection="column" flexGrow={1} overflow="hidden" paddingX={1}>
        {messages.length === 0 && (
          <Box flexDirection="column" marginTop={1} paddingX={2}>
            <Text bold color="cyan">{'  ██╗  ██╗ ███████╗ ██████╗  ███████╗'}</Text>
            <Text bold color="cyan">{'  ██║  ██║ ██╔════╝ ██╔══██╗ ██╔════╝'}</Text>
            <Text bold color="cyan">{'  ███████║ █████╗   ██████╔╝ █████╗'}</Text>
            <Text bold color="cyan">{'  ██╔══██║ ██╔══╝   ██╔══██╗ ██╔══╝'}</Text>
            <Text bold color="cyan">{'  ██║  ██║ ███████╗ ██║  ██║ ███████╗'}</Text>
            <Text bold color="cyan">{'  ╚═╝  ╚═╝ ══════╝ ╚═╝  ╚═╝ ╚══════╝'}</Text>
            <Text> </Text>
            <Text bold color="white">{'  Unified AI Terminal · 4 провайдера'}</Text>
            <Text> </Text>
            <Text dimColor>{'  Напиши сообщение или:'}</Text>
            <Box marginLeft={2}>
              <Text color="yellow">пробел</Text><Text dimColor> голос </Text>
              <Text color="yellow">Ctrl+V</Text><Text dimColor> фото </Text>
              <Text color="yellow">!cmd</Text><Text dimColor> shell </Text>
              <Text color="yellow">/help</Text><Text dimColor> команды</Text>
            </Box>
            <Text> </Text>
          </Box>
        )}
        {visibleMessages.map((msg) => {
          const msgNum = msgNumbers.get(msg.id);
          return (
          <Box key={msg.id} flexDirection="column" marginBottom={0}>
            {msg.role === 'user' && (
              <Box flexDirection="column">
                <Box>
                  <Text dimColor>#{msgNum} </Text>
                  <Text color="cyan" bold>› </Text>
                  <Text>{msg.text}</Text>
                </Box>
                {msg.attachments && msg.attachments.length > 0 && (
                  <Box marginLeft={4} flexDirection="column">
                    {msg.attachments.map((p, i) => (
                      <Text key={i} color="cyan">  📎 Image #{i + 1}: {p.split('/').pop()}</Text>
                    ))}
                  </Box>
                )}
              </Box>
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
                {msg.toolCalls.map((tool) => {
                  const icon = TOOL_ICONS[tool.name] ?? '🔧';
                  const statusIcon = tool.status === 'running' ? '⏳' : tool.status === 'error' ? '✗' : '✓';
                  const statusColor = tool.status === 'running' ? 'yellow' : tool.status === 'error' ? 'red' : 'green';
                  const isExpanded = expandedTools.has(tool.id);
                  const inputPreview = tool.input.replace(/\n/g, ' ').slice(0, 60);
                  const outputLines = tool.output ? tool.output.split('\n') : [];

                  return (
                    <Box key={tool.id} flexDirection="column" marginLeft={1}>
                      <Box>
                        <Text color={statusColor}>{statusIcon} </Text>
                        <Text>{icon} </Text>
                        <Text bold>{tool.name}</Text>
                        <Text dimColor> {inputPreview}</Text>
                        {outputLines.length > 0 && !isExpanded && (
                          <Text dimColor> [{outputLines.length} строк — клик раскрыть]</Text>
                        )}
                        {isExpanded && <Text dimColor> [клик свернуть]</Text>}
                      </Box>
                      {isExpanded && tool.output && (
                        <Box marginLeft={2} flexDirection="column">
                          {outputLines.slice(0, 30).map((line, i) => (
                            <Text key={i} dimColor>{line}</Text>
                          ))}
                          {outputLines.length > 30 && <Text dimColor>… ещё {outputLines.length - 30} строк</Text>}
                        </Box>
                      )}
                    </Box>
                  );
                })}
                {msg.text ? (
                  <Box flexDirection="column">
                    {renderMarkdown(msg.text).map((line, i) => (
                      <Text key={i}>{line}</Text>
                    ))}
                    {msg.streaming && <Text color="yellow"> ▌</Text>}
                  </Box>
                ) : (
                  msg.streaming && msg.toolCalls.length === 0 && (
                    <Text color="yellow">⠋ думаю…</Text>
                  )
                )}
                {!msg.streaming && lastDuration > 0 && msg === messages[messages.length - 1] && (
                  <RunSummary durationMs={lastDuration} tokensIn={lastTokensIn} tokensOut={lastTokensOut} />
                )}
              </Box>
            )}
          </Box>
        ); })}
        {thinking && (
          <Box marginLeft={1}><Text dimColor italic>💭 {thinking.slice(-200)}</Text></Box>
        )}
      </Box>

      <Box borderTop borderStyle="single" flexDirection="column">
        <ChatInput
          onSubmit={handleSubmit}
          onImagePaste={(p) => setAttachments((prev) => [...prev, p])}
          onShellCommand={handleShellCommand}
          onRemoveAttachment={(i) => setAttachments((prev) => prev.filter((_, idx) => idx !== i))}
          attachments={attachments}
          disabled={busy}
          cwd={cwd}
        />
      </Box>
    </Box>
  );
}