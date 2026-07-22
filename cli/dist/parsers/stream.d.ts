import type { StreamEvent, ToolCall } from '../types.js';
/**
 * Парсер stream-json для Claude Code / Qwen Code.
 * Оба CLI пишут совместимый формат: system, assistant, stream_event,
 * tool_use, user, tool_result, rate_limit_event, result.
 */
export declare class ClaudeStreamParser {
    text: string;
    thinking: string;
    toolCalls: ToolCall[];
    sessionId: string | null;
    tokensIn: number;
    tokensOut: number;
    error: string | null;
    private currentToolId;
    private currentToolName;
    private currentToolInput;
    feed(line: string): StreamEvent[];
    private startTool;
    private finishToolInput;
    private endTool;
}
/** Парсер для Gemini CLI (отдельный формат). */
export declare class GeminiStreamParser {
    text: string;
    toolCalls: ToolCall[];
    error: string | null;
    feed(line: string): StreamEvent[];
}
