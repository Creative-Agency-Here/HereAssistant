import React from 'react';
import { Box, Text } from 'ink';
import { renderInlineImage, supportsInlineImages } from '../terminal-images.js';

interface Props {
  type: 'image' | 'video' | 'report';
  path?: string;
  title?: string;
  meta?: string;
  width?: number;
}

const BORDERS = {
  image: { color: 'cyan' as const, icon: '🖼', label: 'IMAGE' },
  video: { color: 'magenta' as const, icon: '🎬', label: 'VIDEO' },
  report: { color: 'green' as const, icon: '📊', label: 'REPORT' },
};

export function FramedMedia({ type, path, title, meta, width = 60 }: Props) {
  const b = BORDERS[type];
  const cols = Math.min(width, (process.stdout.columns || 80) - 4);
  const top = `┌─ ${b.icon} ${b.label} ${title ? `· ${title}` : ''} ${'─'.repeat(Math.max(0, cols - b.label.length - (title?.length ?? 0) - 8))}┐`;
  const bot = `└${'─'.repeat(cols + 2)}┘`;

  return (
    <Box flexDirection="column" marginLeft={1}>
      <Text color={b.color}>{top}</Text>
      {path && supportsInlineImages() && type === 'image' && (
        <Box marginLeft={1}>
          <Text>{renderInlineImage(path, cols - 4)}</Text>
        </Box>
      )}
      {path && type === 'video' && (
        <Box marginLeft={1} flexDirection="column">
          <Text dimColor>  ▶ {path.split('/').pop()}</Text>
          {meta && <Text dimColor>  {meta}</Text>}
        </Box>
      )}
      {path && !supportsInlineImages() && type === 'image' && (
        <Box marginLeft={1}>
          <Text dimColor>  📷 {path.split('/').pop()} {meta ? `(${meta})` : ''}</Text>
        </Box>
      )}
      {meta && type === 'report' && (
        <Box marginLeft={1} flexDirection="column">
          {meta.split('\n').map((line, i) => (
            <Text key={i} dimColor>  {line}</Text>
          ))}
        </Box>
      )}
      <Text color={b.color}>{bot}</Text>
    </Box>
  );
}