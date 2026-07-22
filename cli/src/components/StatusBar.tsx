import React from 'react';
import { Box, Text } from 'ink';

interface Props {
  account: string;
  model: string;
  sessionId: string | null;
  tokensIn: number;
  tokensOut: number;
  cwd: string;
}

export function StatusBar({ account, model, sessionId, tokensIn, tokensOut, cwd }: Props) {
  const project = cwd.split('/').pop() ?? cwd;
  const tokens = tokensIn + tokensOut;

  return (
    <Box borderStyle="single" borderBottom={false} borderLeft={false} borderRight={false} paddingX={1} justifyContent="space-between">
      <Box>
        <Text bold color="magenta">HA</Text>
        <Text dimColor> · </Text>
        <Text>{account}</Text>
        <Text dimColor> · </Text>
        <Text color="cyan">{model || 'default'}</Text>
        {sessionId && (
          <>
            <Text dimColor> · </Text>
            <Text dimColor>{sessionId.slice(0, 8)}</Text>
          </>
        )}
      </Box>
      <Box>
        <Text dimColor>{project}</Text>
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