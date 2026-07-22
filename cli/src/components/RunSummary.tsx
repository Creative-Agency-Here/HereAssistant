import React from 'react';
import { Box, Text } from 'ink';

interface Props {
  durationMs: number;
  tokensIn: number;
  tokensOut: number;
  editsAdded?: number;
  editsRemoved?: number;
}

export function RunSummary({ durationMs, tokensIn, tokensOut, editsAdded, editsRemoved }: Props) {
  const secs = (durationMs / 1000).toFixed(1);
  const parts: string[] = [`${secs}s`];
  if (tokensIn + tokensOut > 0) parts.push(`${((tokensIn + tokensOut) / 1000).toFixed(1)}k tok`);
  if (editsAdded !== undefined || editsRemoved !== undefined) {
    parts.push(`+${editsAdded ?? 0}/-${editsRemoved ?? 0}`);
  }

  return (
    <Box marginTop={0}>
      <Text dimColor italic>── {parts.join(' · ')} ──</Text>
    </Box>
  );
}