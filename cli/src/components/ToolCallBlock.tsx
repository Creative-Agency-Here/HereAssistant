import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';
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

export function ToolCallBlock({ tool, index }: { tool: ToolCall; index: number }) {
  const [expanded, setExpanded] = useState(false);

  useInput((input) => {
    if (input === String(index + 1) && index < 9) setExpanded((e) => !e);
  });

  const icon = toolIcon(tool.name);
  const statusIcon = tool.status === 'running' ? '⏳' : tool.status === 'error' ? '✗' : '✓';
  const statusColor = tool.status === 'running' ? 'yellow' : tool.status === 'error' ? 'red' : 'green';
  const inputPreview = truncate(tool.input, 60);
  const outputLines = tool.output ? tool.output.split('\n').length : 0;

  return (
    <Box flexDirection="column" marginLeft={1}>
      <Box>
        <Text color={statusColor}>{statusIcon} </Text>
        <Text>{icon} </Text>
        <Text bold>{tool.name}</Text>
        <Text dimColor> {inputPreview}</Text>
        {tool.durationMs > 0 && <Text dimColor> {formatDuration(tool.durationMs)}</Text>}
        {outputLines > 0 && !expanded && (
          <Text dimColor> [{outputLines} строк, нажми {index + 1}]</Text>
        )}
        {expanded && <Text dimColor> [свернуть: {index + 1}]</Text>}
      </Box>
      {expanded && tool.output && (
        <Box marginLeft={2} marginTop={0} flexDirection="column">
          {tool.output.split('\n').slice(0, 50).map((line, i) => (
            <Text key={i} dimColor>{line}</Text>
          ))}
          {outputLines > 50 && <Text dimColor>… ещё {outputLines - 50} строк</Text>}
        </Box>
      )}
    </Box>
  );
}