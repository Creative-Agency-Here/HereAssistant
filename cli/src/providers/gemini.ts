import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import path from 'node:path';
import fs from 'node:fs';
import type { Account, ProgressCallback, Provider, ProviderResult } from '../types.js';
import { GeminiStreamParser } from '../parsers/stream.js';

export class GeminiProvider implements Provider {
  constructor(private account: Account) {}

  async run(
    prompt: string,
    cwd: string,
    _sessionId: string | null,
    model: string | null,
    progress: ProgressCallback,
    attachments?: string[],
  ): Promise<ProviderResult> {
    const cliHome = this.account.cli_home_path;
    fs.mkdirSync(cliHome, { recursive: true });

    const env: Record<string, string> = {
      ...process.env as Record<string, string>,
      HOME: cliHome,
      USERPROFILE: cliHome,
      GEMINI_CLI_TRUST_WORKSPACE: 'true',
    };

    const instruction =
      'Отвечай на русском. Будь краток. Shell-команды начинай с rtk для сжатия вывода.';

    let fullPrompt = `${instruction}\n\n---\n\n${prompt}`;
    if (attachments && attachments.length > 0) {
      fullPrompt += '\n\n[Прикреплённые изображения — абсолютные пути]\n';
      for (const p of attachments) fullPrompt += `- ${p}\n`;
    }

    const args = [
      '--skip-trust',
      '--approval-mode', 'yolo',
      '-o', 'stream-json',
      '-p', fullPrompt,
    ];
    if (model) args.push('-m', model);

    const child = spawn('gemini', args, {
      cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    (globalThis as any).__ha_process = child;

    const parser = new GeminiStreamParser();
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
      throw new Error(`gemini failed (rc=${code}): ${stderr.slice(0, 500)}`);
    }

    return { text: parser.text, sessionId: null };
  }
}