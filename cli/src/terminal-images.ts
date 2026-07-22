import fs from 'node:fs';
import { execSync } from 'node:child_process';

export type ImageProtocol = 'iterm2' | 'unicode' | 'none';

/** Определяет протокол inline-изображений по env-переменным. */
export function detectImageProtocol(): ImageProtocol {
  const term = process.env.TERM_PROGRAM || '';
  const lcTerm = process.env.LC_TERMINAL || '';
  const term2 = process.env.TERM || '';

  if (term === 'iTerm.app' || lcTerm === 'iTerm2') return 'iterm2';
  if (term === 'WezTerm' || process.env.WEZTERM_UNIX_SOCKET) return 'iterm2';
  if (term === 'ghostty') return 'iterm2';
  if (process.env.KITTY_WINDOW_ID || term2 === 'xterm-kitty') return 'iterm2'; // Kitty поддерживает iTerm2 protocol
  if (process.env.COLORTERM === 'truecolor' || process.env.COLORTERM === '24bit') return 'unicode';
  return 'none';
}

/** Размеры изображения через sips (macOS) или file header. */
function getImageSize(filePath: string): { width: number; height: number } | null {
  try {
    const out = execSync(`sips -g pixelWidth -g pixelHeight "${filePath}" 2>/dev/null`, {
      encoding: 'utf-8', timeout: 3000,
    });
    const w = out.match(/pixelWidth:\s*(\d+)/);
    const h = out.match(/pixelHeight:\s*(\d+)/);
    if (w && h) return { width: parseInt(w[1]), height: parseInt(h[1]) };
  } catch { /* ignore */ }
  return null;
}

/** iTerm2 inline image protocol. */
function renderITerm2(filePath: string, maxCols: number): string {
  const data = fs.readFileSync(filePath);
  const b64 = data.toString('base64');
  const name = Buffer.from(filePath.split('/').pop() || 'image').toString('base64');
  const size = data.length;

  const dims = getImageSize(filePath);
  const termCols = process.stdout.columns || 80;
  const width = Math.min(maxCols, termCols - 4);
  const widthPx = dims ? Math.round(dims.width * (width / (dims.width / 8))) : width * 8;
  const heightPx = dims ? Math.round(dims.height * (widthPx / dims.width)) : width * 4;

  return `\x1b]1337;File=name=${name};size=${size};width=${widthPx}px;height=${heightPx}px;inline=1:${b64}\x07`;
}

/** Unicode half-block renderer (fallback для truecolor терминалов). */
function renderUnicode(filePath: string, maxCols: number): string {
  // Используем sips для ресайза в маленький PNG, затем читаем пиксели
  // Для простоты — показываем placeholder с размерами
  const dims = getImageSize(filePath);
  const termCols = process.stdout.columns || 80;
  const width = Math.min(maxCols, termCols - 4);
  const height = dims ? Math.max(2, Math.round((dims.height / dims.width) * width * 0.5)) : 4;

  const lines: string[] = [];
  const name = filePath.split('/').pop() || 'image';
  lines.push(`\x1b[2m┌─ ${name} (${dims ? `${dims.width}×${dims.height}` : '?×?'}) ─${'─'.repeat(Math.max(0, width - name.length - 20))}┐\x1b[0m`);

  // Рисуем рамку с placeholder
  for (let y = 0; y < Math.min(height, 6); y++) {
    const row = y === 0 ? '▀' : y === Math.min(height, 6) - 1 ? '▄' : '█';
    lines.push(`\x1b[2m│\x1b[0m\x1b[48;2;40;40;40m${row.repeat(width)}\x1b[0m\x1b[2m│\x1b[0m`);
  }
  lines.push(`\x1b[2m└${'─'.repeat(width + 2)}┘\x1b[0m`);
  lines.push(`\x1b[2m  📷 ${name} — inline-превью требует iTerm2/Kitty/WezTerm\x1b[0m`);

  return lines.join('\n');
}

/** Рендерит изображение для текущего терминала. */
export function renderInlineImage(filePath: string, maxCols = 60): string {
  if (!fs.existsSync(filePath)) return `\x1b[31m✗ файл не найден: ${filePath}\x1b[0m`;

  const protocol = detectImageProtocol();

  switch (protocol) {
    case 'iterm2':
      return renderITerm2(filePath, maxCols);
    case 'unicode':
      return renderUnicode(filePath, maxCols);
    default:
      return `\x1b[2m📷 ${filePath.split('/').pop()} (терминал не поддерживает inline-картинки)\x1b[0m`;
  }
}

/** Проверяет, поддерживает ли текущий терминал inline-картинки. */
export function supportsInlineImages(): boolean {
  return detectImageProtocol() !== 'none';
}