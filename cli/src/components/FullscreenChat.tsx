import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Box, Text, useApp, useInput, useStdout, type DOMElement } from 'ink';
import type { Account, ChatMessage, StreamEvent, ToolCall } from '../types.js';
import { makeProvider } from '../providers/index.js';
import {
  activeProviderProcess,
  signalActiveProviderProcess,
} from '../providers/active-process.js';
import { ChatInput } from './ChatInput.js';
import { StatusBar } from './StatusBar.js';
import { RunSummary } from './RunSummary.js';
import { renderMarkdown } from './markdown.js';
import { handleCommand, type CommandContext } from '../commands.js';
import { createHaSession, loadHaSession, saveHaSession, formatHistoryForPrompt, haSessionTitle, type HaSession } from '../ha-sessions.js';
import { SessionPicker } from './SessionPicker.js';
import { startWorkingTitle, setIdleTitle, stopWorkingTitle } from '../terminal-title.js';
import { cleanClipboardCache, copyTextToClipboard } from '../clipboard.js';
import { loadConfig } from '../config.js';
import { memoryPrompt } from '../memory.js';
import { getTheme } from '../themes.js';
import { renderInlineImage, supportsInlineImages } from '../terminal-images.js';
import { useFullscreen } from '../hooks/useFullscreen.js';
import { useMouse, type MouseEvent } from '../hooks/useMouse.js';
import type { ParsedEscapeKey } from '../mouse-filter.js';
import { execSync, spawn } from 'node:child_process';
import { writeIntegrationState } from '../integration-state.js';

function makeId(): string {
  return Math.random().toString(36).slice(2, 10);
}

const WELCOME_LOGO = [
  '  ██╗  ██╗ ███████╗ ██████╗  ███████╗',
  '  ██║  ██║ ██╔════╝ ██╔══██╗ ██╔════╝',
  '  ███████║ █████╗   ██████╔╝ █████╗',
  '  ██╔══██║ ██╔══╝   ██╔══██╗ ██╔══╝',
  '  ██║  ██║ ███████╗ ██║  ██║ ███████╗',
  '  ╚═╝  ╚═╝ ══════╝ ╚═╝  ╚═╝ ╚══════╝',
];

interface ScreenSelection {
  sr: number;
  sc: number;
  er: number;
  ec: number;
}

interface QueuedPrompt {
  id: string;
  text: string;
  attachments: string[];
}

interface ScreenRowEntry {
  id: string;
  row: number;
  col: number;
  text: string;
  toolId?: string;
}

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

function normalizedScreenSelection(selection: ScreenSelection): ScreenSelection {
  const forward = selection.sr < selection.er
    || (selection.sr === selection.er && selection.sc <= selection.ec);
  return forward
    ? selection
    : { sr: selection.er, sc: selection.ec, er: selection.sr, ec: selection.sc };
}

function screenSelectionRange(
  selection: ScreenSelection | null,
  row: Pick<ScreenRowEntry, 'row' | 'col' | 'text'>,
): [number, number] | null {
  if (!selection) return null;
  const normalized = normalizedScreenSelection(selection);
  if (row.row < normalized.sr || row.row > normalized.er) return null;

  const from = row.row === normalized.sr ? normalized.sc - row.col : 0;
  const to = row.row === normalized.er ? normalized.ec - row.col : row.text.length;
  const start = Math.max(0, Math.min(from, row.text.length));
  const end = Math.max(0, Math.min(to, row.text.length));
  return start < end ? [start, end] : null;
}

function selectedScreenText(rows: ScreenRowEntry[], selection: ScreenSelection | null): string {
  if (!selection) return '';
  const normalized = normalizedScreenSelection(selection);
  const selected: string[] = [];

  for (const row of [...rows].sort((a, b) => a.row - b.row || a.col - b.col)) {
    if (row.row < normalized.sr || row.row > normalized.er) continue;
    const range = screenSelectionRange(normalized, row);
    if (range) selected.push(row.text.slice(range[0], range[1]));
  }
  return selected.join('\n');
}

function SelectableRow({
  id,
  text,
  selection,
  register,
  toolId,
  children,
}: {
  id: string;
  text: string;
  selection: ScreenSelection | null;
  register: (id: string, entry: ScreenRowEntry | null) => void;
  toolId?: string;
  children: React.ReactNode;
}) {
  const ref = useRef<DOMElement | null>(null);
  const [position, setPosition] = useState<{ row: number; col: number } | null>(null);

  React.useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const { left, top } = elementPosition(node);
    const next = { row: top + 1, col: left + 1 };
    setPosition((previous) => (
      previous?.row === next.row && previous.col === next.col ? previous : next
    ));
    register(id, { id, ...next, text, toolId });
  });

  useEffect(() => () => register(id, null), [id, register]);

  const range = position
    ? screenSelectionRange(selection, { ...position, text })
    : null;

  return (
    <Box ref={ref} flexShrink={0}>
      {range ? (
        <Text>
          {text.slice(0, range[0])}
          <Text inverse>{text.slice(range[0], range[1])}</Text>
          {text.slice(range[1])}
        </Text>
      ) : children}
    </Box>
  );
}

export function FullscreenChat({
  account: initialAccount,
  cwd,
  integrationId,
  resumeId,
}: {
  account: Account;
  cwd: string;
  integrationId?: string;
  resumeId?: string;
}) {
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
  const [promptQueue, setPromptQueue] = useState<QueuedPrompt[]>([]);
  const [sessionName, setSessionName] = useState<string | null>(null);
  const [promptCount, setPromptCount] = useState(0);
  const [scrollOffset, setScrollOffset] = useState(0);
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [permMode, setPermMode] = useState(0); // index into PERM_MODES
  const [cancelArmed, setCancelArmed] = useState(false);
  const haSessionRef = useRef<HaSession | null>(null);
  const busyRef = useRef(false);
  const resumedRef = useRef(false);
  const cancelRequestedRef = useRef(false);
  const lastBusyEscapeRef = useRef(0);
  const cancelArmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pickerSessions, setPickerSessions] = useState<HaSession[] | null>(null);
  const project = cwd.split('/').pop() ?? cwd;

  // Своё выделение (как в Claude Code)
  const [selection, setSelection] = useState<ScreenSelection | null>(null);
  const isDraggingRef = useRef(false);
  const screenRowsRef = useRef<Map<string, ScreenRowEntry>>(new Map());
  const registerScreenRow = useCallback((id: string, entry: ScreenRowEntry | null) => {
    if (entry) screenRowsRef.current.set(id, entry);
    else screenRowsRef.current.delete(id);
  }, []);

  // Mouse handler для выделения + кликов
  useEffect(() => {
    const mouseEmitter = (globalThis as any).__ha_mouse as any;
    if (!mouseEmitter) return;

    const handler = (ev: any) => {
      const inputHitTest = (globalThis as Record<string, unknown>).__ha_input_mouse_hit as
        | ((event: MouseEvent) => boolean)
        | undefined;
      if (inputHitTest?.(ev)) {
        isDraggingRef.current = false;
        setSelection(null);
        return;
      }

      if (ev.type === 'press' && ev.button === 'left') {
        isDraggingRef.current = true;
        setSelection({sr: ev.row, sc: ev.col, er: ev.row, ec: ev.col});
      } else if (ev.type === 'move' && ev.button === 'left' && isDraggingRef.current) {
        // Drag обновляет выделение 1:1 с указателем.
        setSelection((prev) => prev ? {...prev, er: ev.row, ec: ev.col} : null);
      } else if (ev.type === 'release' && ev.button === 'left') {
        isDraggingRef.current = false;
        // Release только фиксирует диапазон; копирование — явным Ctrl+C.
        setSelection((prev) => {
          if (!prev) return null;
          const next = {...prev, er: ev.row, ec: ev.col};
          if (next.sr === next.er && next.sc === next.ec) return null;
          return next;
        });
      }
    };

    mouseEmitter.on('event', handler);
    return () => { mouseEmitter.off('event', handler); };
  }, []);

  // Ctrl+C: если есть выделение → копировать, иначе → выход
  useEffect(() => {
    const handler = (ev: any) => {
      if (ev.type === 'press' && ev.button === 'right') {
        // Правый клик = копировать выделение
        if (selection) {
          const text = getSelectedText(selection);
          if (text) {
            copyTextToClipboard(text);
            setSelection(null);
          }
        }
      }
    };
    const mouseEmitter = (globalThis as any).__ha_mouse as any;
    if (mouseEmitter) { mouseEmitter.on('event', handler); return () => mouseEmitter.off('event', handler); }
  }, [selection]);

  const getSelectedText = (currentSelection: typeof selection): string => {
    return selectedScreenText([...screenRowsRef.current.values()], currentSelection);
  };

  const copyCurrentSelection = useCallback((): boolean => {
    const copyInputSelection = (globalThis as Record<string, unknown>).__ha_input_copy_selection as
      | (() => boolean)
      | undefined;
    if (copyInputSelection?.()) return true;
    const text = selectedScreenText([...screenRowsRef.current.values()], selection);
    if (!text) return false;
    if (!copyTextToClipboard(text)) return false;
    setSelection(null);
    return true;
  }, [selection]);

  // Kitty Cmd+C и VS Code extension отправляют единое copy-key событие.
  useEffect(() => {
    const keysEmitter = (globalThis as any).__ha_keys as
      | { on: (event: string, handler: () => void) => void; off: (event: string, handler: () => void) => void }
      | undefined;
    if (!keysEmitter) return;
    const handler = () => { copyCurrentSelection(); };
    keysEmitter.on('copy-key', handler);
    return () => { keysEmitter.off('copy-key', handler); };
  }, [copyCurrentSelection]);

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

  const restoreSession = useCallback((session: HaSession) => {
    haSessionRef.current = session;
    resumedRef.current = true;
    setSessionName(session.name);
    setMessages(session.messages.map((message) => ({
      id: makeId(),
      role: message.role,
      text: message.text,
      toolCalls: [],
      timestamp: message.timestamp,
      streaming: false,
      attachments: message.attachments,
    })));
  }, []);

  useEffect(() => {
    if (!resumeId) return;
    const session = loadHaSession(resumeId);
    if (session) {
      restoreSession(session);
      return;
    }
    addMessage({
      id: makeId(),
      role: 'system',
      text: `✗ сессия ${resumeId.slice(0, 16)} не найдена`,
      toolCalls: [],
      timestamp: Date.now(),
      streaming: false,
    });
  }, [resumeId, restoreSession, addMessage]);

  const doExit = useCallback(() => {
    if (signalActiveProviderProcess('SIGTERM')) cancelRequestedRef.current = true;
    stopWorkingTitle();
    exit();
  }, [exit]);

  const disarmCancellation = useCallback(() => {
    lastBusyEscapeRef.current = 0;
    setCancelArmed(false);
    if (cancelArmTimerRef.current) {
      clearTimeout(cancelArmTimerRef.current);
      cancelArmTimerRef.current = null;
    }
  }, []);

  const handleEscape = useCallback((event: ParsedEscapeKey = { modifiers: 1 }) => {
    const hasShift = ((event.modifiers - 1) & 1) !== 0;
    if (hasShift) return;
    if (pickerSessions) return;
    const inputHasText = (globalThis as Record<string, unknown>).__ha_input_has_text as
      | (() => boolean)
      | undefined;
    if (inputHasText?.()) return;
    if (busy) {
      const now = Date.now();
      if (now - lastBusyEscapeRef.current > 1500) {
        lastBusyEscapeRef.current = now;
        setCancelArmed(true);
        if (cancelArmTimerRef.current) clearTimeout(cancelArmTimerRef.current);
        cancelArmTimerRef.current = setTimeout(() => {
          cancelArmTimerRef.current = null;
          lastBusyEscapeRef.current = 0;
          setCancelArmed(false);
        }, 1500);
        return;
      }
      const proc = activeProviderProcess();
      if (proc && signalActiveProviderProcess('SIGINT')) {
        cancelRequestedRef.current = true;
        const forceTimer = setTimeout(() => {
          if (proc.exitCode === null && proc.signalCode === null) {
            try { proc.kill('SIGTERM'); } catch { /* процесс уже завершился */ }
          }
        }, 750);
        forceTimer.unref();
      }
      disarmCancellation();
      return;
    }
    disarmCancellation();
    if (resumedRef.current) {
      resumedRef.current = false;
      haSessionRef.current = null;
      setSessionName(null);
      setPromptCount(0);
      setMessages([{
        id: makeId(),
        role: 'system',
        text: '▸ выход из продолженной сессии — начат новый диалог',
        toolCalls: [],
        timestamp: Date.now(),
        streaming: false,
      }]);
    }
  }, [busy, disarmCancellation, pickerSessions]);

  useEffect(() => {
    if (!busy) disarmCancellation();
    return () => {
      if (cancelArmTimerRef.current) clearTimeout(cancelArmTimerRef.current);
    };
  }, [busy, disarmCancellation]);

  useEffect(() => {
    const keysEmitter = (globalThis as any).__ha_keys as
      | { on: (event: string, handler: (event: ParsedEscapeKey) => void) => void; off: (event: string, handler: (event: ParsedEscapeKey) => void) => void }
      | undefined;
    if (!keysEmitter) return;
    keysEmitter.on('escape-key', handleEscape);
    return () => { keysEmitter.off('escape-key', handleEscape); };
  }, [handleEscape]);

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
      const hit = [...screenRowsRef.current.values()]
        .find((entry) => entry.row === event.row && entry.toolId);
      if (hit?.toolId) toggleTool(hit.toolId);
    }
    if (event.type === 'scroll') {
      setScrollOffset((prev) => Math.max(0, prev + (event.button === 'scroll-up' ? -3 : 3)));
    }
  });

  const handleSubmit = useCallback(async (value: string, queuedAttachments?: string[]) => {
    const text = value.trim();
    if (!text) return;
    const submissionAttachments = queuedAttachments ?? attachments;

    if (busyRef.current) {
      setPromptQueue((previous) => [
        ...previous,
        { id: makeId(), text, attachments: [...submissionAttachments] },
      ]);
      setAttachments([]);
      return;
    }

    if (text.startsWith('/')) {
      const ctx: CommandContext = {
        account, model, sessionId: haSessionRef.current?.id ?? null, cwd,
        tokensIn, tokensOut,
        setModel: (m) => setModel(m),
        setAccount: (a) => { setAccount(a); setModel(a.default_model || ''); },
        resetSession: () => {
          haSessionRef.current = createHaSession(cwd);
          resumedRef.current = false;
          setMessages([]);
          setSessionName(null);
        },
        resumeSession: (id) => {
          const session = loadHaSession(id);
          if (!session) { addMessage({ id: makeId(), role: 'system', text: `✗ сессия ${id.slice(0, 16)} не найдена`, toolCalls: [], timestamp: Date.now(), streaming: false }); return; }
          restoreSession(session);
        },
        openSessionPicker: (sessions) => setPickerSessions(sessions),
        renameSession: (name) => {
          setSessionName(name);
          setIdleTitle(name, promptCount);
          if (haSessionRef.current) { haSessionRef.current.name = name; saveHaSession(haSessionRef.current); }
        },
        forkSession: () => {
          const forked = createHaSession(cwd);
          if (haSessionRef.current) {
            forked.messages = [...haSessionRef.current.messages];
            forked.name = haSessionRef.current.name ? `${haSessionRef.current.name} (fork)` : null;
          }
          saveHaSession(forked);
          haSessionRef.current = forked;
          resumedRef.current = false;
          setSessionName(forked.name);
        },
        setTheme: () => {},
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
              if (!copyTextToClipboard(last.text)) throw new Error('clipboard недоступен');
              addMessage({ id: makeId(), role: 'system', text: '📋 скопировано в clipboard', toolCalls: [], timestamp: Date.now(), streaming: false });
            } catch {
              addMessage({ id: makeId(), role: 'system', text: '✗ не удалось скопировать', toolCalls: [], timestamp: Date.now(), streaming: false });
            }
          } else {
            addMessage({ id: makeId(), role: 'system', text: '✗ нет ответа для копирования', toolCalls: [], timestamp: Date.now(), streaming: false });
          }
        },
        insertAtCursor: (t) => {
          // Через глобальный ref — ChatInput читает и вставляет
          (globalThis as Record<string, unknown>).__ha_insert = t;
        },
        print: (t) => addMessage({ id: makeId(), role: 'system', text: t, toolCalls: [], timestamp: Date.now(), streaming: false }),
        exit: doExit,
        attachImage: (p) => setAttachments((prev) => [...prev, p]),
      };
      if (handleCommand(text, ctx)) return;
    }

    // После первого нового сообщения ESC снова отвечает только за отмену задачи:
    // история уже стала продолжением текущего разговора.
    resumedRef.current = false;

    // HA-сессия: создаём при первом сообщении
    if (!haSessionRef.current) {
      haSessionRef.current = createHaSession(cwd);
    }
    const session = haSessionRef.current;

    // Авто-имя сессии по первому сообщению
    if (!session.name && session.messages.length === 0) {
      session.name = text.slice(0, 60);
      setSessionName(session.name);
    }

    // Сохраняем пользовательское сообщение
    session.messages.push({
      role: 'user', text, timestamp: Date.now(),
      provider: account.provider, model: model || undefined,
      attachments: submissionAttachments.length > 0 ? [...submissionAttachments] : undefined,
    });

    // Форматируем историю для провайдера (все кроме текущего)
    const historyPrompt = formatHistoryForPrompt(session.messages.slice(0, -1));

    addMessage({ id: makeId(), role: 'user', text, toolCalls: [], timestamp: Date.now(), streaming: false, attachments: submissionAttachments.length > 0 ? [...submissionAttachments] : undefined });
    const assistantMsg: ChatMessage = {
      id: makeId(), role: 'assistant', text: '', toolCalls: [], timestamp: Date.now(), streaming: true,
    };
    addMessage(assistantMsg);
    busyRef.current = true;
    setBusy(true);
    setThinking('');
    setLastDuration(0);
    setPromptCount((c) => c + 1);
    startWorkingTitle(sessionName || project, promptCount + 1);
    if (integrationId) writeIntegrationState(integrationId, { state: 'working', cwd, title: text, taskCount: promptCount + 1 });
    const t0 = Date.now();
    cancelRequestedRef.current = false;
    let timeout: ReturnType<typeof setTimeout> | null = null;

    try {
      const provider = makeProvider(account);
      const currentAttachments = [...submissionAttachments];
      setAttachments([]);
      const fullPrompt = memory ? `${text}${memory}` : text;

      const TIMEOUT_MS = 5 * 60 * 1000;
      const result = await Promise.race([
        provider.run(fullPrompt, cwd, null, model || null, (event: StreamEvent) => {
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
        }, currentAttachments, historyPrompt || undefined),
        new Promise<never>((_, reject) => {
          timeout = setTimeout(() => {
            signalActiveProviderProcess('SIGTERM');
            reject(new Error('Таймаут: провайдер не ответил за 5 минут'));
          }, TIMEOUT_MS);
          timeout.unref();
        }),
      ]);

      const duration = Date.now() - t0;
      if (result.tokensIn) { setTokensIn((p) => p + result.tokensIn!); setLastTokensIn(result.tokensIn); }
      if (result.tokensOut) { setTokensOut((p) => p + result.tokensOut!); setLastTokensOut(result.tokensOut); }
      setLastDuration(duration);
      updateLastAssistant((m) => ({ ...m, text: result.text || m.text, streaming: false }));

      // Сохраняем ответ ассистента в HA-сессию
      session.messages.push({
        role: 'assistant', text: result.text || '', timestamp: Date.now(),
        provider: account.provider, model: model || undefined,
      });
      saveHaSession(session);
    } catch (err) {
      const cancelled = cancelRequestedRef.current;
      const message = cancelled
        ? '⏹ текущая работа отменена'
        : `✗ ${err instanceof Error ? err.message : String(err)}`;
      setLastDuration(Date.now() - t0);
      updateLastAssistant((m) => ({
        ...m, text: message, streaming: false,
      }));
      session.messages.push({
        role: 'assistant',
        text: cancelled ? 'Запрос отменён пользователем.' : message,
        timestamp: Date.now(),
        provider: account.provider,
        model: model || undefined,
      });
      saveHaSession(session);
    } finally {
      if (timeout) clearTimeout(timeout);
      cancelRequestedRef.current = false;
      busyRef.current = false;
      setBusy(false);
      setThinking('');
      setIdleTitle(sessionName || project, promptCount);
      if (integrationId) writeIntegrationState(integrationId, { state: 'open', cwd, taskCount: promptCount, sessionId: haSessionRef.current?.id ?? null });
    }
  }, [account, cwd, model, tokensIn, tokensOut, project, promptCount, sessionName, attachments, memory, addMessage, updateLastAssistant, doExit]);

  useEffect(() => {
    if (busy || busyRef.current || promptQueue.length === 0) return;
    const [next] = promptQueue;
    setPromptQueue((previous) => previous.slice(1));
    void handleSubmit(next.text, next.attachments);
  }, [busy, promptQueue, handleSubmit]);

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
    // Пока открыт пикер, клавиатурой владеет SessionPicker.
    if (pickerSessions) return;

    // ESC — отменить текущую задачу
    if (key.escape) {
      handleEscape({ modifiers: key.shift ? 2 : 1 });
      return;
    }
    if (key.meta && input.toLowerCase() === 'c') {
      copyCurrentSelection();
      return;
    }
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
        sessionId={haSessionRef.current?.id ?? null}
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
        {pickerSessions && (
          <SessionPicker
            sessions={pickerSessions}
            onSelect={(id) => {
              setPickerSessions(null);
              const session = loadHaSession(id);
              if (!session) return;
              restoreSession(session);
            }}
            onCancel={() => setPickerSessions(null)}
          />
        )}
        {!pickerSessions && messages.length === 0 && (
          <Box flexDirection="column" marginTop={1} paddingX={2}>
            {WELCOME_LOGO.map((line, index) => (
              <SelectableRow
                key={index}
                id={`welcome-logo-${index}`}
                text={line}
                selection={selection}
                register={registerScreenRow}
              >
                <Text bold color="cyan">{line}</Text>
              </SelectableRow>
            ))}
            <Text> </Text>
            <SelectableRow
              id="welcome-title"
              text="  Unified AI Terminal · 4 провайдера"
              selection={selection}
              register={registerScreenRow}
            >
              <Text bold color="white">{'  Unified AI Terminal · 4 провайдера'}</Text>
            </SelectableRow>
            <Text> </Text>
            <SelectableRow
              id="welcome-prompt"
              text="  Напиши сообщение или:"
              selection={selection}
              register={registerScreenRow}
            >
              <Text dimColor>{'  Напиши сообщение или:'}</Text>
            </SelectableRow>
            <Box marginLeft={2}>
              <SelectableRow
                id="welcome-commands"
                text="пробел голос Ctrl+V фото !cmd shell /help команды"
                selection={selection}
                register={registerScreenRow}
              >
                <Text color="yellow">пробел</Text><Text dimColor> голос </Text>
                <Text color="yellow">Ctrl+V</Text><Text dimColor> фото </Text>
                <Text color="yellow">!cmd</Text><Text dimColor> shell </Text>
                <Text color="yellow">/help</Text><Text dimColor> команды</Text>
              </SelectableRow>
            </Box>
            <Text> </Text>
          </Box>
        )}
        {!pickerSessions && visibleMessages.map((msg) => {
          const msgNum = msgNumbers.get(msg.id);
          return (
          <Box key={msg.id} flexDirection="column" marginBottom={0}>
            {msg.role === 'user' && (
              <Box flexDirection="column">
                {msg.text.split('\n').map((line, lineIdx) => {
                  const prefix = lineIdx === 0 ? `#${msgNum} › ` : '    ';
                  const rowText = prefix + line;
                  return (
                    <SelectableRow
                      key={lineIdx}
                      id={`user-${msg.id}-${lineIdx}`}
                      text={rowText}
                      selection={selection}
                      register={registerScreenRow}
                    >
                      <Text>
                        <Text dimColor>{lineIdx === 0 ? `#${msgNum} ` : '    '}</Text>
                        {lineIdx === 0 && <Text color="cyan" bold>› </Text>}
                        <Text>{line}</Text>
                      </Text>
                    </SelectableRow>
                  );
                })}
                {msg.attachments && msg.attachments.length > 0 && (
                  <Box marginLeft={4} flexDirection="column">
                    {msg.attachments.map((p, i) => (
                      <SelectableRow
                        key={i}
                        id={`attachment-${msg.id}-${i}`}
                        text={`  📎 Image #${i + 1}: ${p.split('/').pop()}`}
                        selection={selection}
                        register={registerScreenRow}
                      >
                        <Text color="cyan">  📎 Image #{i + 1}: {p.split('/').pop()}</Text>
                      </SelectableRow>
                    ))}
                  </Box>
                )}
              </Box>
            )}
            {msg.role === 'system' && (
              <Box flexDirection="column">
                {msg.text.split('\n').map((line, i) => (
                  <SelectableRow
                    key={i}
                    id={`system-${msg.id}-${i}`}
                    text={line}
                    selection={selection}
                    register={registerScreenRow}
                  >
                    <Text dimColor>{line}</Text>
                  </SelectableRow>
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
                      <SelectableRow
                        id={`tool-${msg.id}-${tool.id}`}
                        text={`${statusIcon} ${icon} ${tool.name} ${inputPreview}`}
                        selection={selection}
                        register={registerScreenRow}
                        toolId={tool.id}
                      >
                        <Text>
                          <Text color={statusColor}>{statusIcon} </Text>
                          <Text>{icon} </Text>
                          <Text bold>{tool.name}</Text>
                          <Text dimColor> {inputPreview}</Text>
                          {outputLines.length > 0 && !isExpanded && <Text dimColor> [{outputLines.length} строк — клик раскрыть]</Text>}
                          {isExpanded && <Text dimColor> [клик свернуть]</Text>}
                        </Text>
                      </SelectableRow>
                      {isExpanded && tool.output && (
                        <Box marginLeft={2} flexDirection="column">
                          {outputLines.slice(0, 30).map((line, i) => (
                            <SelectableRow
                              key={i}
                              id={`tool-output-${msg.id}-${tool.id}-${i}`}
                              text={line}
                              selection={selection}
                              register={registerScreenRow}
                            >
                              <Text dimColor>{line}</Text>
                            </SelectableRow>
                          ))}
                          {outputLines.length > 30 && <Text dimColor>… ещё {outputLines.length - 30} строк</Text>}
                        </Box>
                      )}
                    </Box>
                  );
                })}
                {msg.text ? (
                  <Box flexDirection="column">
                    {renderMarkdown(msg.text).map((line, i) => {
                      const plainLine = line.replace(/\x1b\[[0-9;]*m/g, '');
                      return (
                        <SelectableRow
                          key={i}
                          id={`assistant-${msg.id}-${i}`}
                          text={plainLine}
                          selection={selection}
                          register={registerScreenRow}
                        >
                          <Text>{line}</Text>
                        </SelectableRow>
                      );
                    })}
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
        {!pickerSessions && thinking && (
          <Box marginLeft={1}><Text dimColor italic>💭 {thinking.slice(-200)}</Text></Box>
        )}
      </Box>

      <Box borderTop borderStyle="single" flexDirection="column">
        {cancelArmed && (
          <Box paddingX={1}>
            <Text color="yellow">Esc ещё раз — отменить текущую работу</Text>
          </Box>
        )}
        {promptQueue.length > 0 && (
          <Box paddingX={1}>
            <Text dimColor>
              очередь: {promptQueue.length} · {promptQueue[0].text.slice(0, Math.max(10, termCols - 24))}
            </Text>
          </Box>
        )}
        <ChatInput
          onSubmit={handleSubmit}
          onImagePaste={(p) => setAttachments((prev) => [...prev, p])}
          onShellCommand={handleShellCommand}
          onRemoveAttachment={(i) => setAttachments((prev) => prev.filter((_, idx) => idx !== i))}
          attachments={attachments}
          disabled={!!pickerSessions}
          placeholder={busy ? 'агент работает… Enter добавит запрос в очередь' : undefined}
          cwd={cwd}
        />
      </Box>
    </Box>
  );
}
