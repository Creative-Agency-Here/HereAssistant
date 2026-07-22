import React from 'react';
import { Box, Text } from 'ink';
import type { ToolCall } from '../types.js';

const TOOL_ICONS: Record<string, string> = {
  read_file: '📄', write_file: '✏️', edit: '✏️',
  run_shell_command: '⚡', grep_search: '🔍', glob: '📁',
  agent: '🤖', ask_user_question: '❓', todo_write: '📋',
  web_fetch: '🌐', notebook_edit: '📓',
};

function toolIcon(name: string): string {
  return TOOL_ICONS[name] ?? '🔧';
}

function truncate(text: string, max: number): string {
  const clean = text.replace(/\n/g, ' ').replace(/\s+/g, ' ').trim();
  return clean.length <= max ? clean : clean.slice(0, max) + '…';
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const PREVIEW_LINES = 3;

export function ToolCallBlock({ tool }: { tool: ToolCall }) {
  const icon = toolIcon(tool.name);
  const statusIcon = tool.status === 'running' ? '⏳' : tool.status === 'error' ? '✗' : '✓';
  const statusColor = tool.status === 'running' ? 'yellow' : tool.status === 'error' ? 'red' : 'green';
  const inputPreview = truncate(tool.input, 60);
  const outputLines = tool.output ? tool.output.split('\n') : [];

  return (
    <Box flexDirection="column" marginLeft={1}>
      <Box>
        <Text color={statusColor}>{statusIcon} </Text>
        <Text>{icon} </Text>
        <Text bold>{tool.name}</Text>
        <Text dimColor> {inputPreview}</Text>
        {tool.durationMs > 0 && <Text dimColor> {formatDuration(tool.durationMs)}</Text>}
      </Box>
      {outputLines.length > 0 && (
        <Box marginLeft={2} flexDirection="column">
          {outputLines.slice(0, PREVIEW_LINES).map((line, i) => (
            <Text key={i} dimColor>{line}</Text>
          ))}
          {outputLines.length > PREVIEW_LINES && (
            <Text dimColor>… ещё {outputLines.length - PREVIEW_LINES} строк</Text>
          )}
        </Box>
      )}
    </Box>
  );
}