import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import path from 'node:path';
import fs from 'node:fs';
import type { Account, ProgressCallback, Provider, ProviderResult } from '../types.js';
import { ClaudeStreamParser } from '../parsers/stream.js';

const INHERITED_PROVIDER_ENV = [
  'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'DASHSCOPE_API_KEY',
  'BAILIAN_CODING_PLAN_API_KEY', 'BAILIAN_TOKEN_PLAN_API_KEY',
  'GEMINI_API_KEY', 'GOOGLE_API_KEY',
];

export class QwenCodeProvider implements Provider {
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
    const qwenHome = path.join(cliHome, '.qwen');
    fs.mkdirSync(qwenHome, { recursive: true });

    const env: Record<string, string> = { ...process.env as Record<string, string> };
    for (const key of INHERITED_PROVIDER_ENV) delete env[key];
    env.QWEN_HOME = qwenHome;
    env.QWEN_RUNTIME_DIR = qwenHome;

    const args = [
      '--output-format', 'stream-json',
      '--include-partial-messages',
      '--approval-mode', 'auto',
      '--append-system-prompt',
      'Отвечай на русском. Будь краток. Shell-команды начинай с rtk для сжатия вывода.',
      '--prompt', '',
    ];
    if (model) args.push('--model', model);
    if (sessionId) args.push('--resume', sessionId);

    const child = spawn('qwen', args, {
      cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    child.stdin.write(prompt);
    if (attachments && attachments.length > 0) {
      child.stdin.write('\n\n[Прикреплённые изображения — абсолютные пути]\n');
      for (const p of attachments) child.stdin.write(`- ${p}\n`);
    }
    child.stdin.end();

    const parser = new ClaudeStreamParser();

    const rl = createInterface({ input: child.stdout! });
    rl.on('line', (line) => {
      const events = parser.feed(line);
      for (const event of events) progress(event);
    });

    let stderr = '';
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });

    const code = await new Promise<number>((resolve) => {
      child.on('close', (c) => resolve(c ?? 1));
    });

    if (code !== 0 && !parser.text) {
      throw new Error(`qwen failed (rc=${code}): ${stderr.slice(0, 500)}`);
    }

    return {
      text: parser.text,
      sessionId: parser.sessionId,
      tokensIn: parser.tokensIn,
      tokensOut: parser.tokensOut,
    };
  }
}