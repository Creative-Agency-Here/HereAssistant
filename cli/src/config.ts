import fs from 'node:fs';
import path from 'node:path';

export interface HaConfig {
  defaultProvider?: string;
  defaultModel?: string;
  defaultAccount?: string;
  approvalMode?: string;
  theme?: string;
  plainMode?: boolean;
  mouseSupport?: boolean;
  profiles?: Record<string, Partial<HaConfig>>;
}

const CONFIG_NAMES = ['.hereassistant/config.json', '.ha/config.json'];

export function loadConfig(cwd: string, profile?: string): HaConfig {
  for (const name of CONFIG_NAMES) {
    const fp = path.join(cwd, name);
    if (fs.existsSync(fp)) {
      try {
        const raw = JSON.parse(fs.readFileSync(fp, 'utf-8')) as HaConfig;
        if (profile && raw.profiles?.[profile]) {
          return { ...raw, ...raw.profiles[profile] };
        }
        return raw;
      } catch { /* skip malformed */ }
    }
  }
  return {};
}

export function saveConfig(cwd: string, config: HaConfig): void {
  const dir = path.join(cwd, '.hereassistant');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'config.json'),
    JSON.stringify(config, null, 2) + '\n',
    'utf-8',
  );
}

export function listProfiles(cwd: string): string[] {
  for (const name of CONFIG_NAMES) {
    const fp = path.join(cwd, name);
    if (fs.existsSync(fp)) {
      try {
        const raw = JSON.parse(fs.readFileSync(fp, 'utf-8')) as HaConfig;
        return Object.keys(raw.profiles || {});
      } catch { /* skip */ }
    }
  }
  return [];
}