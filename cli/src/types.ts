export interface Account {
  id: number;
  provider: string;
  label: string;
  default_model: string;
  enabled: number;
  cli_home_path: string;
  notes: string;
  owner_user_id: number;
  shared: number;
}

export interface ToolCall {
  id: string;
  name: string;
  input: string;
  status: 'running' | 'done' | 'error';
  output: string;
  durationMs: number;
  collapsed: boolean;
}

export interface StreamEvent {
  type: string;
  [key: string]: unknown;
}

export interface ProviderResult {
  text: string;
  sessionId: string | null;
  tokensIn?: number;
  tokensOut?: number;
}

export type ProgressCallback = (event: StreamEvent) => void;

export interface Provider {
  run(
    prompt: string,
    cwd: string,
    sessionId: string | null,
    model: string | null,
    progress: ProgressCallback,
    attachments?: string[],
  ): Promise<ProviderResult>;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;
  toolCalls: ToolCall[];
  timestamp: number;
  streaming: boolean;
  attachments?: string[];
}