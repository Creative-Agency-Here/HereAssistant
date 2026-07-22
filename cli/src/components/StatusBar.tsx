import React from 'react';
import { Box, Text } from 'ink';

interface Props {
  account: string;
  model: string;
  sessionId: string | null;
  sessionName: string | null;
  tokensIn: number;
  tokensOut: number;
  cwd: string;
  provider: string;
  taskCount: number;
  busy: boolean;
  permMode?: string;
}

export function StatusBar({ account, model, sessionId, sessionName, tokensIn, tokensOut, cwd, provider, taskCount, busy, permMode }: Props) {
  const project = cwd.split('/').pop() ?? cwd;
  const tokens = tokensIn + tokensOut;
  const label = sessionName || project;

  return (
    <Box borderStyle="single" borderBottom={false} borderLeft={false} borderRight={false} paddingX={1} justifyContent="space-between">
      <Box>
        <Text bold color="magenta">HA</Text>
        <Text dimColor> · </Text>
        <Text>{account}</Text>
        <Text dimColor> · </Text>
        <Text color="cyan">{model || 'default'}</Text>
        <Text dimColor> · </Text>
        <Text color="yellow">{permMode || 'ask'}</Text>
        <Text dimColor> · Shift+Tab</Text>
        {sessionName && (
          <>
            <Text dimColor> · </Text>
            <Text color="yellow">{sessionName}</Text>
          </>
        )}
        {sessionId && !sessionName && (
          <>
            <Text dimColor> · </Text>
            <Text dimColor>{sessionId.slice(0, 8)}</Text>
          </>
        )}
      </Box>
      <Box>
        {busy && <Text color="yellow">⚙ </Text>}
        <Text bold>{label}</Text>
        <Text dimColor> · </Text>
        <Text color={taskCount > 0 ? 'green' : undefined}>{taskCount} задач</Text>
        {tokens > 0 && (
          <>
            <Text dimColor> · </Text>
            <Text dimColor>{(tokens / 1000).toFixed(1)}k tok</Text>
          </>
        )}
      </Box>
    </Box>
  );
}