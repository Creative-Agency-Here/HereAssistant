import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';
import type { ToolCall } from '../types.js';

const TOOL_ICONS: Record<string, string> = {
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

function toolIcon(name: string): string {
  return TOOL_ICONS[name] ?? '🔧';
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + '…';
}

export function ToolCallBlock({ tool, index }: { tool: ToolCall; index: number }) {
  const [expanded, setExpanded] = useState(false);

  useInput((input) => {
    if (input === String(index + 1)) setExpanded((e) => !e);
  });

  const icon = toolIcon(tool.name);
  const statusIcon = tool.status === 'running' ? '⏳' : tool.status === 'error' ? '✗' : '✓';
  const inputPreview = truncate(tool.input.replace(/\n/g, ' '), 60);

  return (
    <Box flexDirection="column" marginLeft={1}>
      <Box>
        <Text dimColor>{statusIcon} </Text>
        <Text>{icon} </Text>
        <Text bold>{tool.name}</Text>
        <Text dimColor> {inputPreview}</Text>
        {tool.output && !expanded && (
          <Text dimColor> [{tool.output.split('\n').length} строк]</Text>
        )}
      </Box>
      {expanded && tool.output && (
        <Box marginLeft={2} marginTop={0}>
          <Text dimColor>{truncate(tool.output, 2000)}</Text>
        </Box>
      )}
    </Box>
  );
}