/**
 * Markdown → ANSI для терминала.
 * Заголовки, жирный, курсив, inline-код, блоки кода с подсветкой,
 * списки, ссылки, цитаты, thinking-блоки.
 */

const BOLD = '\x1b[1m';
const DIM = '\x1b[2m';
const ITALIC = '\x1b[3m';
const CYAN = '\x1b[36m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const MAGENTA = '\x1b[35m';
const BLUE = '\x1b[34m';
const RED = '\x1b[31m';
const RESET = '\x1b[0m';

const KEYWORDS = new Set([
  'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while',
  'class', 'import', 'export', 'from', 'async', 'await', 'try', 'catch',
  'throw', 'new', 'this', 'typeof', 'instanceof', 'in', 'of', 'switch',
  'case', 'break', 'continue', 'default', 'yield', 'static', 'extends',
  'super', 'interface', 'type', 'enum', 'implements', 'public', 'private',
  'protected', 'readonly', 'abstract', 'declare', 'namespace', 'module',
  'def', 'elif', 'lambda', 'with', 'as', 'pass', 'raise', 'except',
  'finally', 'assert', 'global', 'nonlocal', 'del', 'print',
  'fn', 'pub', 'mut', 'impl', 'trait', 'struct', 'match', 'loop',
  'func', 'package', 'go', 'defer', 'chan', 'select', 'range',
  'do', 'done', 'then', 'fi', 'esac', 'echo', 'cd', 'ls', 'grep',
  'sudo', 'npm', 'npx', 'git', 'python', 'node', 'pip',
]);

const STRINGS_RE = /(["'`])(?:(?!\1|\\).|\\.)*\1/g;
const COMMENTS_RE = /(\/\/.*$|#.*$)/gm;
const NUMBERS_RE = /\b(\d+\.?\d*)\b/g;

function highlightLine(line: string): string {
  let result = line;
  // Comments
  result = result.replace(COMMENTS_RE, `${DIM}$1${RESET}`);
  // Strings
  result = result.replace(STRINGS_RE, (m) => `${GREEN}${m}${RESET}`);
  // Numbers
  result = result.replace(NUMBERS_RE, (m) => `${YELLOW}${m}${RESET}`);
  // Keywords
  result = result.replace(/\b([a-zA-Z_]\w*)\b/g, (m, word: string) => {
    if (KEYWORDS.has(word)) return `${BLUE}${word}${RESET}`;
    return m;
  });
  return result;
}

function inlineFormat(text: string): string {
  const codeSpans: string[] = [];
  let result = text.replace(/`([^`]+)`/g, (_m, code: string) => {
    codeSpans.push(code);
    return `\x00CODE${codeSpans.length - 1}\x00`;
  });
  result = result
    .replace(/\*\*([^*]+)\*\*/g, `${BOLD}$1${RESET}`)
    .replace(/\*([^*]+)\*/g, `${ITALIC}$1${RESET}`)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, `${MAGENTA}$1${RESET} ${DIM}($2)${RESET}`);
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
      out.push(highlightLine(line));
      continue;
    }

    // Thinking block
    if (line.trimStart().startsWith('<thinking>') || line.trimStart().startsWith('💭')) {
      out.push(`${DIM}${ITALIC}💭 ${line.replace(/<\/?thinking>/g, '').trim()}${RESET}`);
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