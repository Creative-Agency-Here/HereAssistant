import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import path from 'node:path';
import fs from 'node:fs';
import type { Account, ProgressCallback, Provider, ProviderResult } from '../types.js';
import { ClaudeStreamParser } from '../parsers/stream.js';

export class ClaudeCodeProvider implements Provider {
  constructor(private account: Account) {}

  async run(
    prompt: string,
    cwd: string,
    sessionId: string | null,
    model: string | null,
    progress: ProgressCallback,
  ): Promise<ProviderResult> {
    const cliHome = this.account.cli_home_path;
    fs.mkdirSync(cliHome, { recursive: true });

    const env: Record<string, string> = {
      ...process.env as Record<string, string>,
      CLAUDE_CONFIG_DIR: cliHome,
    };

    const args = [
      '--print',
      '--output-format', 'stream-json',
      '--verbose',
      '--include-partial-messages',
      '--permission-mode', 'acceptEdits',
      '--append-system-prompt',
      'Отвечай на русском. Будь краток. Shell-команды начинай с rtk для сжатия вывода.',
    ];
    if (model) args.push('--model', model);
    if (sessionId) args.push('--resume', sessionId);

    const child = spawn('claude', args, {
      cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    child.stdin.write(prompt);
    child.stdin.end();

    const parser = new ClaudeStreamParser();
    const rl = createInterface({ input: child.stdout! });
    rl.on('line', (line) => {
      for (const event of parser.feed(line)) progress(event);
    });

    let stderr = '';
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });

    const code = await new Promise<number>((resolve) => {
      child.on('close', (c) => resolve(c ?? 1));
    });

    if (code !== 0 && !parser.text) {
      throw new Error(`claude failed (rc=${code}): ${stderr.slice(0, 500)}`);
    }

    return {
      text: parser.text,
      sessionId: parser.sessionId,
      tokensIn: parser.tokensIn,
      tokensOut: parser.tokensOut,
    };
  }
}