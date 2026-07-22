import type { Account, Provider } from '../types.js';
import { ClaudeCodeProvider } from './claude.js';
import { CodexProvider } from './codex.js';
import { GeminiProvider } from './gemini.js';
import { QwenCodeProvider } from './qwen.js';

const REGISTRY: Record<string, new (account: Account) => Provider> = {
  claude_code: ClaudeCodeProvider,
  codex: CodexProvider,
  gemini: GeminiProvider,
  qwen_code: QwenCodeProvider,
};

export function makeProvider(account: Account): Provider {
  const Ctor = REGISTRY[account.provider];
  if (!Ctor) {
    throw new Error(`Провайдер "${account.provider}" не поддерживается в TUI`);
  }
  return new Ctor(account);
}

export const PROVIDER_NAMES = Object.keys(REGISTRY);