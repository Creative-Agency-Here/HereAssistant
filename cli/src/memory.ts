import fs from 'node:fs';
import path from 'node:path';

const MEMORY_FILES = [
  'CLAUDE.md',
  '.hereassistant/memory.md',
  '.hereassistant/context.md',
  'AGENTS.md',
];

const MAX_CHARS = 4000;

/** Загружает файлы памяти из cwd и родительских директорий. */
export function loadMemory(cwd: string): string {
  const parts: string[] = [];
  let dir = cwd;

  // Идём вверх до корня или home
  const home = process.env.HOME || '/';
  while (dir && dir !== home && dir !== '/') {
    for (const name of MEMORY_FILES) {
      const fp = path.join(dir, name);
      if (fs.existsSync(fp)) {
        try {
          const content = fs.readFileSync(fp, 'utf-8').trim();
          if (content) {
            parts.push(`# ${name} (${path.basename(dir)})\n${content}`);
          }
        } catch { /* skip */ }
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }

  if (parts.length === 0) return '';

  let combined = parts.join('\n\n---\n\n');
  if (combined.length > MAX_CHARS) {
    combined = combined.slice(0, MAX_CHARS) + '\n\n… (обрезано, ' + combined.length + ' символов всего)';
  }

  return combined;
}

/** Формирует блок памяти для system prompt. */
export function memoryPrompt(cwd: string): string {
  const memory = loadMemory(cwd);
  if (!memory) return '';
  return `\n\n## Память проекта (только для чтения)\n${memory}`;
}