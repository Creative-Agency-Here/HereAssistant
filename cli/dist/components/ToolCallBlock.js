import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useState } from 'react';
import { Box, Text, useInput } from 'ink';
const TOOL_ICONS = {
    read_file: '📄',
    write_file: '✏️',
    edit: '✏️',
    run_shell_command: '⚡',
    grep_search: '🔍',
    glob: '📁',
    agent: '🤖',
    ask_user_question: '❓',
    todo_write: '📋',
};
function toolIcon(name) {
    return TOOL_ICONS[name] ?? '🔧';
}
function truncate(text, max) {
    if (text.length <= max)
        return text;
    return text.slice(0, max) + '…';
}
export function ToolCallBlock({ tool, index }) {
    const [expanded, setExpanded] = useState(false);
    useInput((input) => {
        if (input === String(index + 1))
            setExpanded((e) => !e);
    });
    const icon = toolIcon(tool.name);
    const statusIcon = tool.status === 'running' ? '⏳' : tool.status === 'error' ? '✗' : '✓';
    const inputPreview = truncate(tool.input.replace(/\n/g, ' '), 60);
    return (_jsxs(Box, { flexDirection: "column", marginLeft: 1, children: [_jsxs(Box, { children: [_jsxs(Text, { dimColor: true, children: [statusIcon, " "] }), _jsxs(Text, { children: [icon, " "] }), _jsx(Text, { bold: true, children: tool.name }), _jsxs(Text, { dimColor: true, children: [" ", inputPreview] }), tool.output && !expanded && (_jsxs(Text, { dimColor: true, children: [" [", tool.output.split('\n').length, " \u0441\u0442\u0440\u043E\u043A]"] }))] }), expanded && tool.output && (_jsx(Box, { marginLeft: 2, marginTop: 0, children: _jsx(Text, { dimColor: true, children: truncate(tool.output, 2000) }) }))] }));
}
//# sourceMappingURL=ToolCallBlock.js.map