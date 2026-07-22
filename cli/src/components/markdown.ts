/**
 * Лёгкий markdown → ANSI для терминала.
 * Не полный парсер — покрывает типичный вывод AI-агентов:
 * заголовки, жирный, курсив, inline-код, блоки кода, списки, ссылки.
 */

const BOLD = '\x1b[1m';
const DIM = '\x1b[2m';
const ITALIC = '\x1b[3m';
const CYAN = '\x1b[36m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const MAGENTA = '\x1b[35m';
const RESET = '\x1b[0m';

function inlineFormat(text: string): string {
  // Сначала защищаем inline-код от дальнейшего форматирования
  const codeSpans: string[] = [];
  let result = text.replace(/`([^`]+)`/g, (_m, code: string) => {
    codeSpans.push(code);
    return `\x00CODE${codeSpans.length - 1}\x00`;
  });
  result = result
    .replace(/\*\*([^*]+)\*\*/g, `${BOLD}$1${RESET}`)
    .replace(/\*([^*]+)\*/g, `${ITALIC}$1${RESET}`)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, `${MAGENTA}$1${RESET} ${DIM}($2)${RESET}`);
  // Возвращаем код обратно
  result = result.replace(/\x00CODE(\d+)\x00/g, (_m, idx: string) => {
    return `${CYAN}${codeSpans[Number(idx)]}${RESET}`;
  });
  return result;
}

export function renderMarkdown(source: string): string[] {
  const lines = source.split('\n');
  const out: string[] = [];
  let inCodeBlock = false;
  let codeLang = '';

  for (const raw of lines) {
    const line = raw;

    if (line.trimStart().startsWith('```')) {
      if (!inCodeBlock) {
        inCodeBlock = true;
        codeLang = line.trimStart().slice(3).trim();
        out.push(`${DIM}${codeLang ? `── ${codeLang} ` : '──'}${'─'.repeat(Math.max(0, 40 - codeLang.length))}${RESET}`);
      } else {
        inCodeBlock = false;
        out.push(`${DIM}${'─'.repeat(42)}${RESET}`);
      }
      continue;
    }

    if (inCodeBlock) {
      out.push(`${GREEN}${line}${RESET}`);
      continue;
    }

    const headingMatch = line.match(/^(#{1,4})\s+(.+)/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const text = headingMatch[2];
      if (level === 1) out.push(`${BOLD}${MAGENTA}${text}${RESET}`);
      else if (level === 2) out.push(`${BOLD}${CYAN}${text}${RESET}`);
      else out.push(`${BOLD}${text}${RESET}`);
      continue;
    }

    const listMatch = line.match(/^(\s*)([-*+]|\d+\.)\s+(.+)/);
    if (listMatch) {
      const indent = listMatch[1];
      const marker = listMatch[2];
      const text = listMatch[3];
      const bullet = marker.match(/\d/) ? `${YELLOW}${marker}${RESET}` : `${CYAN}•${RESET}`;
      out.push(`${indent}${bullet} ${inlineFormat(text)}`);
      continue;
    }

    if (line.trimStart().startsWith('> ')) {
      out.push(`${DIM}│ ${inlineFormat(line.trimStart().slice(2))}${RESET}`);
      continue;
    }

    if (line.trim() === '') {
      out.push('');
      continue;
    }

    out.push(inlineFormat(line));
  }

  return out;
}