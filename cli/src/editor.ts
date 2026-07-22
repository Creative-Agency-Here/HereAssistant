import { execSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

/** Открывает текст во внешнем редакторе ($VISUAL / $EDITOR / nano). */
export function openInEditor(initialText: string): string | null {
  const editor = process.env.VISUAL || process.env.EDITOR || 'nano';
  const tmpFile = path.join(os.tmpdir(), `ha-editor-${Date.now()}.md`);

  try {
    fs.writeFileSync(tmpFile, initialText, 'utf-8');

    // Открываем редактор, наследуя stdio для интерактивности
    const result = spawnSync(editor, [tmpFile], {
      stdio: 'inherit',
      timeout: 300_000, // 5 минут
    });

    if (result.status !== 0) return null;

    const edited = fs.readFileSync(tmpFile, 'utf-8').trim();
    return edited || null;
  } catch {
    return null;
  } finally {
    try { fs.unlinkSync(tmpFile); } catch { /* ignore */ }
  }
}