import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';

const CACHE_DIR = path.join(os.tmpdir(), 'ha-clipboard');

/** Сохраняет изображение из clipboard в temp-файл. Возвращает путь или null. */
export function pasteImageFromClipboard(): string | null {
  if (process.platform !== 'darwin') return null;

  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const id = crypto.randomBytes(4).toString('hex');
  const filePath = path.join(CACHE_DIR, `${id}.png`);

  try {
    const script = `
      try
        set imgData to the clipboard as «class PNGf»
        set fp to open for access POSIX file "${filePath}" with write permission
        set eof fp to 0
        write imgData to fp
        close access fp
        return "ok"
      on error
        try
          close access POSIX file "${filePath}"
        end try
        return "no_image"
      end try
    `;
    const result = execSync(`osascript -e '${script.replace(/'/g, "'\\''")}'`, {
      encoding: 'utf-8',
      timeout: 5000,
    }).trim();

    if (result === 'ok' && fs.existsSync(filePath) && fs.statSync(filePath).size > 0) {
      return filePath;
    }
  } catch {
    // Clipboard doesn't contain image
  }

  // Cleanup empty file
  try { fs.unlinkSync(filePath); } catch { /* ignore */ }
  return null;
}

/** Проверяет, есть ли изображение в clipboard (без сохранения). */
export function hasClipboardImage(): boolean {
  if (process.platform !== 'darwin') return false;
  try {
    const result = execSync(
      `osascript -e 'try\nthe clipboard as «class PNGf»\nreturn "yes"\non error\nreturn "no"\nend try'`,
      { encoding: 'utf-8', timeout: 3000 },
    ).trim();
    return result === 'yes';
  } catch {
    return false;
  }
}

/** Очищает старые файлы из кеша (> 1 час). */
export function cleanClipboardCache(): void {
  if (!fs.existsSync(CACHE_DIR)) return;
  const now = Date.now();
  for (const file of fs.readdirSync(CACHE_DIR)) {
    const fp = path.join(CACHE_DIR, file);
    try {
      if (now - fs.statSync(fp).mtimeMs > 3600_000) fs.unlinkSync(fp);
    } catch { /* ignore */ }
  }
}