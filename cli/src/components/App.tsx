import React, { useEffect, useState } from 'react';
import { EventEmitter } from 'node:events';
import { Box, Text, useInput } from 'ink';
import type { Account } from '../types.js';
import { getAccounts } from '../db.js';
import { FullscreenChat } from './FullscreenChat.js';
import type { ParsedNavigationKey } from '../mouse-filter.js';

function AccountPicker({ onSelect }: { onSelect: (a: Account) => void }) {
  const accounts = getAccounts();
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    const keysEmitter = (globalThis as Record<string, unknown>).__ha_keys as EventEmitter | undefined;
    if (!keysEmitter) return;
    const onArrow = ({ direction }: ParsedNavigationKey) => {
      if (direction === 'up') setCursor((current) => Math.max(0, current - 1));
      if (direction === 'down') setCursor((current) => Math.min(accounts.length - 1, current + 1));
      if (direction === 'home') setCursor(0);
      if (direction === 'end') setCursor(Math.max(0, accounts.length - 1));
    };
    keysEmitter.on('arrow-key', onArrow);
    return () => { keysEmitter.off('arrow-key', onArrow); };
  }, [accounts.length]);

  useInput((input, key) => {
    if (key.upArrow) setCursor((c) => Math.max(0, c - 1));
    if (key.downArrow) setCursor((c) => Math.min(accounts.length - 1, c + 1));
    if (key.return && accounts[cursor]) onSelect(accounts[cursor]);
    if (input === 'q') process.exit(0);
  });

  return (
    <Box flexDirection="column" padding={1}>
      <Text bold color="magenta">HereAssistant · выбери аккаунт</Text>
      <Box flexDirection="column" marginTop={1}>
        {accounts.map((a, i) => (
          <Box key={a.id}>
            <Text color={i === cursor ? 'cyan' : undefined}>
              {i === cursor ? '❯ ' : '  '}
            </Text>
            <Text bold={i === cursor}>{a.label}</Text>
            <Text dimColor> · {a.provider} · {a.default_model || 'default'}</Text>
            {a.notes ? <Text dimColor> · {a.notes}</Text> : null}
          </Box>
        ))}
      </Box>
      <Box marginTop={1}>
        <Text dimColor>↑↓ выбор · Enter подтвердить · q выход</Text>
      </Box>
    </Box>
  );
}

export function App({ preselected, resumeId, integrationId }: { preselected?: string; resumeId?: string; integrationId?: string }) {
  const [account, setAccount] = useState<Account | null>(() => {
    if (!preselected) return null;
    const accounts = getAccounts();
    return accounts.find((a) => a.label === preselected) ?? null;
  });

  if (!account) {
    return <AccountPicker onSelect={setAccount} />;
  }

  return <FullscreenChat account={account} cwd={process.cwd()} integrationId={integrationId} resumeId={resumeId} />;
}
