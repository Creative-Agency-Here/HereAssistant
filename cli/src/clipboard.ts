import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = path.join(os.tmpdir(), 'ha-clipboard');
const CLIPBOARD_IMG_BIN = path.join(__dirname, '..', 'bin', 'clipboard_img');

/** Сохраняет изображение из clipboard в temp-файл. Возвращает путь или null.
 *  Использует нативный Swift-бинарник (не требует osascript automation permissions). */
export function pasteImageFromClipboard(): string | null {
  if (process.platform !== 'darwin') return null;

  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const id = crypto.randomBytes(4).toString('hex');
  const filePath = path.join(CACHE_DIR, `${id}.png`);

  try {
    const result = spawnSync(CLIPBOARD_IMG_BIN, [filePath], { timeout: 5000 });
    if (result.status === 0 && fs.existsSync(filePath) && fs.statSync(filePath).size > 0) {
      return filePath;
    }
  } catch { /* ignore */ }

  try { fs.unlinkSync(filePath); } catch { /* ignore */ }
  return null;
}

/** Проверяет, есть ли изображение в clipboard (без сохранения). */
export function hasClipboardImage(): boolean {
  if (process.platform !== 'darwin') return false;
  const tmp = path.join(CACHE_DIR, '_check.png');
  try {
    const result = spawnSync(CLIPBOARD_IMG_BIN, [tmp], { timeout: 3000 });
    if (result.status === 0) {
      try { fs.unlinkSync(tmp); } catch { /* ignore */ }
      return true;
    }
  } catch { /* ignore */ }
  return false;
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