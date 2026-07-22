import type { Account, ProgressCallback, Provider, ProviderResult } from '../types.js';
export declare class QwenCodeProvider implements Provider {
    private account;
    constructor(account: Account);
    run(prompt: string, cwd: string, sessionId: string | null, model: string | null, progress: ProgressCallback): Promise<ProviderResult>;
}
