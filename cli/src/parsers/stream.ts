import type { StreamEvent, ToolCall } from '../types.js';

/**
 * Парсер stream-json для Claude Code / Qwen Code.
 * Оба CLI пишут совместимый формат: system, assistant, stream_event,
 * tool_use, user, tool_result, rate_limit_event, result.
 */
export class ClaudeStreamParser {
  text = '';
  thinking = '';
  toolCalls: ToolCall[] = [];
  sessionId: string | null = null;
  tokensIn = 0;
  tokensOut = 0;
  error: string | null = null;

  private currentToolId: string | null = null;
  private currentToolName = '';
  private currentToolInput = '';

  feed(line: string): StreamEvent[] {
    const events: StreamEvent[] = [];
    const trimmed = line.trim();
    if (!trimmed) return events;

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(trimmed);
    } catch {
      return events;
    }

    const type = String(data.type ?? '');

    switch (type) {
      case 'system': {
        const sid = data.session_id ?? data.sessionId;
        if (sid) this.sessionId = String(sid);
        break;
      }

      case 'assistant': {
        const msg = data.message as Record<string, unknown> | undefined;
        if (!msg) break;
        const content = msg.content;
        if (typeof content === 'string') {
          this.text += content;
          events.push({ type: 'text', text: content });
        } else if (Array.isArray(content)) {
          for (const block of content) {
            const b = block as Record<string, unknown>;
            if (b.type === 'text' && typeof b.text === 'string') {
              this.text += b.text;
              events.push({ type: 'text', text: b.text });
            } else if (b.type === 'thinking' && typeof b.thinking === 'string') {
              this.thinking += b.thinking;
              events.push({ type: 'thinking', text: b.thinking });
            } else if (b.type === 'tool_use') {
              this.startTool(String(b.id ?? ''), String(b.name ?? ''), JSON.stringify(b.input ?? {}));
              events.push({ type: 'tool_start', tool: this.toolCalls[this.toolCalls.length - 1] });
            }
          }
        }
        break;
      }

      case 'stream_event': {
        const ev = data.event as Record<string, unknown> | undefined;
        if (!ev) break;
        const evType = String(ev.type ?? '');
        if (evType === 'content_block_delta') {
          const delta = ev.delta as Record<string, unknown> | undefined;
          if (delta?.type === 'text_delta' && typeof delta.text === 'string') {
            this.text += delta.text;
            events.push({ type: 'text', text: delta.text });
          } else if (delta?.type === 'thinking_delta' && typeof delta.thinking === 'string') {
            this.thinking += delta.thinking;
            events.push({ type: 'thinking', text: delta.thinking });
          }
        } else if (evType === 'content_block_start') {
          const cb = ev.content_block as Record<string, unknown> | undefined;
          if (cb?.type === 'tool_use') {
            this.startTool(String(cb.id ?? ''), String(cb.name ?? ''), '');
            events.push({ type: 'tool_start', tool: this.toolCalls[this.toolCalls.length - 1] });
          }
        } else if (evType === 'content_block_stop' && this.currentToolId) {
          this.finishToolInput();
        }
        break;
      }

      case 'tool_use': {
        this.startTool(String(data.id ?? ''), String(data.name ?? ''), JSON.stringify(data.input ?? {}));
        events.push({ type: 'tool_start', tool: this.toolCalls[this.toolCalls.length - 1] });
        break;
      }

      case 'tool_result': {
        const toolId = String(data.tool_use_id ?? data.id ?? '');
        const content = data.content;
        let output = '';
        if (typeof content === 'string') output = content;
        else if (Array.isArray(content)) {
          output = content
            .map((b: unknown) => {
              const block = b as Record<string, unknown>;
              return block.type === 'text' ? String(block.text ?? '') : '';
            })
            .join('\n');
        }
        const isError = Boolean(data.is_error);
        this.endTool(toolId, output, isError);
        events.push({ type: 'tool_end', toolId, output, isError });
        break;
      }

      case 'user': {
        const msg = data.message as Record<string, unknown> | undefined;
        if (!msg) break;
        const content = msg.content;
        if (Array.isArray(content)) {
          for (const block of content) {
            const b = block as Record<string, unknown>;
            if (b.type === 'tool_result') {
              const toolId = String(b.tool_use_id ?? '');
              let output = '';
              const rc = b.content;
              if (typeof rc === 'string') output = rc;
              else if (Array.isArray(rc)) {
                output = rc
                  .map((x: unknown) => {
                    const xb = x as Record<string, unknown>;
                    return xb.type === 'text' ? String(xb.text ?? '') : '';
                  })
                  .join('\n');
              }
              this.endTool(toolId, output, Boolean(b.is_error));
              events.push({ type: 'tool_end', toolId, output, isError: Boolean(b.is_error) });
            }
          }
        }
        break;
      }

      case 'result': {
        const usage = data.usage as Record<string, unknown> | undefined;
        if (usage) {
          this.tokensIn = Number(usage.input_tokens ?? 0);
          this.tokensOut = Number(usage.output_tokens ?? 0);
        }
        if (data.is_error) {
          this.error = String(data.result ?? data.error ?? 'unknown error');
        }
        events.push({ type: 'result', text: this.text });
        break;
      }

      case 'rate_limit_event': {
        events.push({ type: 'rate_limit', ...data });
        break;
      }
    }

    return events;
  }

  private startTool(id: string, name: string, input: string) {
    this.currentToolId = id;
    this.currentToolName = name;
    this.currentToolInput = input;
    this.toolCalls.push({
      id,
      name,
      input,
      status: 'running',
      output: '',
      durationMs: 0,
      collapsed: true,
    });
  }

  private finishToolInput() {
    if (this.currentToolId && this.toolCalls.length > 0) {
      const last = this.toolCalls[this.toolCalls.length - 1];
      if (last.id === this.currentToolId) {
        last.input = this.currentToolInput;
      }
    }
    this.currentToolId = null;
  }

  private endTool(toolId: string, output: string, isError: boolean) {
    const tool = this.toolCalls.find((t) => t.id === toolId);
    if (tool) {
      tool.status = isError ? 'error' : 'done';
      tool.output = output;
    }
  }
}

/** Парсер для Gemini CLI (отдельный формат). */
export class GeminiStreamParser {
  text = '';
  toolCalls: ToolCall[] = [];
  error: string | null = null;

  feed(line: string): StreamEvent[] {
    const events: StreamEvent[] = [];
    const trimmed = line.trim();
    if (!trimmed) return events;

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(trimmed);
    } catch {
      return events;
    }

    const type = String(data.type ?? '');

    switch (type) {
      case 'message': {
        const content = data.content;
        if (typeof content === 'string') {
          this.text += content;
          events.push({ type: 'text', text: content });
        } else if (Array.isArray(content)) {
          for (const block of content) {
            const b = block as Record<string, unknown>;
            if (b.type === 'text' && typeof b.text === 'string') {
              this.text += b.text;
              events.push({ type: 'text', text: b.text });
            }
          }
        }
        break;
      }
      case 'tool_use': {
        const id = String(data.id ?? `gemini-${Date.now()}`);
        this.toolCalls.push({
          id,
          name: String(data.name ?? ''),
          input: JSON.stringify(data.input ?? {}),
          status: 'running',
          output: '',
          durationMs: 0,
          collapsed: true,
        });
        events.push({ type: 'tool_start', tool: this.toolCalls[this.toolCalls.length - 1] });
        break;
      }
      case 'tool_result': {
        const toolId = String(data.id ?? '');
        const tool = this.toolCalls.find((t) => t.id === toolId);
        if (tool) {
          tool.status = data.is_error ? 'error' : 'done';
          tool.output = String(data.output ?? '');
        }
        events.push({ type: 'tool_end', toolId });
        break;
      }
      case 'result': {
        if (data.error) this.error = String(data.error);
        events.push({ type: 'result', text: this.text });
        break;
      }
    }

    return events;
  }
}