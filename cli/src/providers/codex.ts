import { spawn } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';
import type { Account, ProgressCallback, Provider, ProviderResult } from '../types.js';

/** Извлекает session ID из вывода codex (эвристика). */
function extractSessionId(out: string, err: string, fallback: string | null): string | null {
  const combined = out + '\n' + err;
  const match = combined.match(/session[_\s-]*id[:\s]+([a-f0-9-]{8,})/i);
  return match ? match[1] : fallback;
}

export class CodexProvider implements Provider {
  constructor(private account: Account) {}

  async run(
    prompt: string,
    cwd: string,
    sessionId: string | null,
    model: string | null,
    progress: ProgressCallback,
    attachments?: string[],
  ): Promise<ProviderResult> {
    const cliHome = this.account.cli_home_path;
    fs.mkdirSync(cliHome, { recursive: true });

    const env: Record<string, string> = {
      ...process.env as Record<string, string>,
      CODEX_HOME: cliHome,
    };

    const instructions =
      'Отвечай на русском. Будь краток. Shell-команды начинай с rtk для сжатия вывода.';

    let args: string[];
    if (sessionId) {
      args = ['codex', 'exec', 'resume', sessionId, '--skip-git-repo-check'];
    } else {
      args = ['codex', 'exec', '--skip-git-repo-check'];
    }
    if (model) args.push('-c', `model=${model}`);
    args.push('-c', `instructions=${JSON.stringify(instructions)}`);
    if (attachments) {
      for (const img of attachments) args.push('-i', img);
    }
    args.push(prompt);

    const child = spawn(args[0], args.slice(1), {
      cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    (globalThis as any).__ha_process = child;

    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      stdout += text;
      progress({ type: 'text', text });
    });
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });

    const code = await new Promise<number>((resolve) => {
      child.on('close', (c) => resolve(c ?? 1));
    });

    if (code !== 0 && !stdout.trim()) {
      throw new Error(`codex failed (rc=${code}): ${stderr.slice(0, 500)}`);
    }

    return {
      text: stdout.trim(),
      sessionId: extractSessionId(stdout, stderr, sessionId),
    };
  }
}