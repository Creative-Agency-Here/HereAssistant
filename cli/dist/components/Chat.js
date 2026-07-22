import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useRef, useCallback } from 'react';
import { Box, Text, useApp, useInput, useStdin } from 'ink';
import TextInput from 'ink-text-input';
import Spinner from 'ink-spinner';
import { QwenCodeProvider } from '../providers/qwen.js';
import { ToolCallBlock } from './ToolCallBlock.js';
const PROVIDER_MAP = {
    qwen_code: (a) => new QwenCodeProvider(a),
};
function makeId() {
    return Math.random().toString(36).slice(2, 10);
}
export function Chat({ account, cwd }) {
    const { exit } = useApp();
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [busy, setBusy] = useState(false);
    const [model] = useState(account.default_model || '');
    const sessionIdRef = useRef(null);
    const { stdin } = useStdin();
    const addMessage = useCallback((msg) => {
        setMessages((prev) => [...prev, msg]);
    }, []);
    const updateLastAssistant = useCallback((updater) => {
        setMessages((prev) => {
            const idx = prev.length - 1;
            if (idx < 0 || prev[idx].role !== 'assistant')
                return prev;
            const next = [...prev];
            next[idx] = updater(next[idx]);
            return next;
        });
    }, []);
    const handleSubmit = useCallback(async (value) => {
        const text = value.trim();
        if (!text)
            return;
        setInput('');
        if (text === '/exit' || text === '/quit') {
            exit();
            return;
        }
        addMessage({
            id: makeId(),
            role: 'user',
            text,
            toolCalls: [],
            timestamp: Date.now(),
            streaming: false,
        });
        const assistantMsg = {
            id: makeId(),
            role: 'assistant',
            text: '',
            toolCalls: [],
            timestamp: Date.now(),
            streaming: true,
        };
        addMessage(assistantMsg);
        setBusy(true);
        const provider = PROVIDER_MAP[account.provider];
        if (!provider) {
            updateLastAssistant((m) => ({
                ...m,
                text: `Провайдер "${account.provider}" пока не поддерживается в TUI.`,
                streaming: false,
            }));
            setBusy(false);
            return;
        }
        try {
            const result = await provider(account).run(text, cwd, sessionIdRef.current, model || null, (event) => {
                if (event.type === 'text' && typeof event.text === 'string') {
                    updateLastAssistant((m) => ({ ...m, text: m.text + event.text }));
                }
                else if (event.type === 'tool_start' && event.tool) {
                    const tool = event.tool;
                    updateLastAssistant((m) => ({
                        ...m,
                        toolCalls: [...m.toolCalls, { ...tool }],
                    }));
                }
                else if (event.type === 'tool_end') {
                    const toolId = String(event.toolId ?? '');
                    const output = event.output != null ? String(event.output) : '';
                    const isError = Boolean(event.isError);
                    updateLastAssistant((m) => ({
                        ...m,
                        toolCalls: m.toolCalls.map((t) => t.id === toolId
                            ? { ...t, status: isError ? 'error' : 'done', output: String(output ?? '') }
                            : t),
                    }));
                }
            });
            if (result.sessionId)
                sessionIdRef.current = result.sessionId;
            updateLastAssistant((m) => ({
                ...m,
                text: result.text || m.text,
                streaming: false,
            }));
        }
        catch (err) {
            updateLastAssistant((m) => ({
                ...m,
                text: `✗ Ошибка: ${err instanceof Error ? err.message : String(err)}`,
                streaming: false,
            }));
        }
        finally {
            setBusy(false);
        }
    }, [account, cwd, model, addMessage, updateLastAssistant, exit]);
    useInput((input, key) => {
        if (key.ctrl && input === 'c') {
            exit();
        }
    });
    return (_jsxs(Box, { flexDirection: "column", height: "100%", children: [_jsxs(Box, { borderStyle: "single", borderBottom: false, borderLeft: false, borderRight: false, paddingX: 1, children: [_jsx(Text, { bold: true, color: "magenta", children: "HereAssistant" }), _jsxs(Text, { dimColor: true, children: [" \u00B7 ", account.label] }), _jsxs(Text, { dimColor: true, children: [" \u00B7 ", model || 'default'] }), _jsxs(Text, { dimColor: true, children: [" \u00B7 ", cwd.split('/').pop()] })] }), _jsx(Box, { flexDirection: "column", flexGrow: 1, overflow: "hidden", children: messages.map((msg) => (_jsx(Box, { flexDirection: "column", marginBottom: 1, children: msg.role === 'user' ? (_jsxs(Box, { children: [_jsx(Text, { color: "cyan", bold: true, children: "\u203A " }), _jsx(Text, { children: msg.text })] })) : (_jsxs(Box, { flexDirection: "column", children: [msg.toolCalls.map((tool, i) => (_jsx(ToolCallBlock, { tool: tool, index: i }, tool.id))), msg.text && (_jsxs(Box, { marginTop: msg.toolCalls.length > 0 ? 1 : 0, children: [_jsx(Text, { children: msg.text }), msg.streaming && _jsx(Text, { color: "yellow", children: " \u258C" })] })), msg.streaming && !msg.text && msg.toolCalls.length === 0 && (_jsx(Box, { children: _jsxs(Text, { color: "yellow", children: [_jsx(Spinner, { type: "dots" }), " \u0434\u0443\u043C\u0430\u044E\u2026"] }) }))] })) }, msg.id))) }), _jsx(Box, { borderTop: true, borderStyle: "single", paddingX: 1, children: busy ? (_jsxs(Text, { dimColor: true, children: [_jsx(Spinner, { type: "dots" }), " \u0430\u0433\u0435\u043D\u0442 \u0440\u0430\u0431\u043E\u0442\u0430\u0435\u0442\u2026"] })) : (_jsxs(Box, { children: [_jsx(Text, { color: "magenta", bold: true, children: "\u203A " }), _jsx(TextInput, { value: input, onChange: setInput, onSubmit: handleSubmit, placeholder: "\u043D\u0430\u043F\u0438\u0448\u0438 \u0441\u043E\u043E\u0431\u0449\u0435\u043D\u0438\u0435\u2026 (/exit \u2014 \u0432\u044B\u0445\u043E\u0434)" })] })) })] }));
}
//# sourceMappingURL=Chat.js.map