import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { Box, Text, useInput } from 'ink';
import { getAccounts } from '../db.js';
import { Chat } from './Chat.js';
function AccountPicker({ onSelect }) {
    const accounts = getAccounts();
    const [cursor, setCursor] = useState(0);
    useInput((input, key) => {
        if (key.upArrow)
            setCursor((c) => Math.max(0, c - 1));
        if (key.downArrow)
            setCursor((c) => Math.min(accounts.length - 1, c + 1));
        if (key.return && accounts[cursor])
            onSelect(accounts[cursor]);
        if (input === 'q')
            process.exit(0);
    });
    return (_jsxs(Box, { flexDirection: "column", padding: 1, children: [_jsx(Text, { bold: true, color: "magenta", children: "HereAssistant \u00B7 \u0432\u044B\u0431\u0435\u0440\u0438 \u0430\u043A\u043A\u0430\u0443\u043D\u0442" }), _jsx(Box, { flexDirection: "column", marginTop: 1, children: accounts.map((a, i) => (_jsxs(Box, { children: [_jsx(Text, { color: i === cursor ? 'cyan' : undefined, children: i === cursor ? '❯ ' : '  ' }), _jsx(Text, { bold: i === cursor, children: a.label }), _jsxs(Text, { dimColor: true, children: [" \u00B7 ", a.provider, " \u00B7 ", a.default_model || 'default'] }), a.notes ? _jsxs(Text, { dimColor: true, children: [" \u00B7 ", a.notes] }) : null] }, a.id))) }), _jsx(Box, { marginTop: 1, children: _jsx(Text, { dimColor: true, children: "\u2191\u2193 \u0432\u044B\u0431\u043E\u0440 \u00B7 Enter \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u00B7 q \u0432\u044B\u0445\u043E\u0434" }) })] }));
}
export function App({ preselected }) {
    const [account, setAccount] = useState(() => {
        if (!preselected)
            return null;
        const accounts = getAccounts();
        return accounts.find((a) => a.label === preselected) ?? null;
    });
    if (!account) {
        return _jsx(AccountPicker, { onSelect: setAccount });
    }
    return _jsx(Chat, { account: account, cwd: process.cwd() });
}
//# sourceMappingURL=App.js.map