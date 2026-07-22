import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

/** Пишет integration state файл (как Python core/integration_state.py).
 *  VS Code расширение читает эти файлы для TreeView сессий. */

const STATE_DIR = path.join(
  process.env.HA_STATE_DIR || path.join(os.homedir(), 'Visual Studio Code', 'Creative Agency Here', 'HereAssistant', '.runtime', 'state', 'integrations'),
);

export interface IntegrationState {
  version: number;
  state: 'open' | 'working' | 'error' | 'closed';
  cwd: string;
  taskCount: number;
  title: string | null;
  sessionId: string | null;
  preview: string | null;
  updatedAt: number;
}

export function writeIntegrationState(
  integrationId: string,
  partial: Partial<IntegrationState> & { state: IntegrationState['state']; cwd: string },
): void {
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    const fp = path.join(STATE_DIR, `${integrationId}.json`);
    const payload: IntegrationState = {
      version: 1,
      state: partial.state,
      cwd: partial.cwd,
      taskCount: partial.taskCount ?? 0,
      title: partial.title ?? null,
      sessionId: partial.sessionId ?? null,
      preview: partial.preview ?? null,
      updatedAt: Math.floor(Date.now() / 1000),
    };
    const tmp = fp + `.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 0), 'utf-8');
    fs.renameSync(tmp, fp);
  } catch { /* best effort */ }
}