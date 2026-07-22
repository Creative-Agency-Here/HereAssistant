import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import path from 'node:path';
import fs from 'node:fs';
import { ClaudeStreamParser } from '../parsers/stream.js';
const INHERITED_PROVIDER_ENV = [
    'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'DASHSCOPE_API_KEY',
    'BAILIAN_CODING_PLAN_API_KEY', 'BAILIAN_TOKEN_PLAN_API_KEY',
    'GEMINI_API_KEY', 'GOOGLE_API_KEY',
];
export class QwenCodeProvider {
    account;
    constructor(account) {
        this.account = account;
    }
    async run(prompt, cwd, sessionId, model, progress) {
        const cliHome = this.account.cli_home_path;
        const qwenHome = path.join(cliHome, '.qwen');
        fs.mkdirSync(qwenHome, { recursive: true });
        const env = { ...process.env };
        for (const key of INHERITED_PROVIDER_ENV)
            delete env[key];
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
        if (model)
            args.push('--model', model);
        if (sessionId)
            args.push('--resume', sessionId);
        const child = spawn('qwen', args, {
            cwd,
            env,
            stdio: ['pipe', 'pipe', 'pipe'],
        });
        child.stdin.write(prompt);
        child.stdin.end();
        const parser = new ClaudeStreamParser();
        const rl = createInterface({ input: child.stdout });
        rl.on('line', (line) => {
            const events = parser.feed(line);
            for (const event of events)
                progress(event);
        });
        let stderr = '';
        child.stderr?.on('data', (chunk) => { stderr += chunk.toString(); });
        const code = await new Promise((resolve) => {
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
//# sourceMappingURL=qwen.js.map