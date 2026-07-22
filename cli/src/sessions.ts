import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export interface SessionInfo {
  id: string;
  title: string;
  provider: string;
  cwd: string;
  updatedAt: number;
  preview: string;
}

/** Читает сессии Claude Code из ~/.claude/projects/<slug>/*.jsonl */
function readClaudeSessions(cliHome: string, cwd: string): SessionInfo[] {
  const slug = cwd.replace(/[^a-zA-Z0-9]/g, '-');
  const dir = path.join(cliHome, 'projects', slug);
  if (!fs.existsSync(dir)) return [];

  const sessions: SessionInfo[] = [];
  try {
    for (const file of fs.readdirSync(dir).filter((f) => f.endsWith('.jsonl'))) {
      const fp = path.join(dir, file);
      const stat = fs.statSync(fp);
      const id = file.replace('.jsonl', '');

      // Читаем первые строки для title и preview
      let title = '';
      let preview = '';
      try {
        const content = fs.readFileSync(fp, 'utf-8');
        const lines = content.split('\n').filter(Boolean);
        for (const line of lines.slice(0, 10)) {
          try {
            const obj = JSON.parse(line);
            if (obj.type === 'user' && !title) {
              const msg = obj.message;
              if (msg?.content) {
                const text = typeof msg.content === 'string'
                  ? msg.content
                  : Array.isArray(msg.content)
                    ? msg.content.filter((b: { type: string }) => b.type === 'text').map((b: { text: string }) => b.text).join(' ')
                    : '';
                title = text.slice(0, 80);
              }
            }
            if (obj.type === 'assistant' && !preview) {
              const msg = obj.message;
              if (msg?.content) {
                const text = typeof msg.content === 'string'
                  ? msg.content
                  : Array.isArray(msg.content)
                    ? msg.content.filter((b: { type: string }) => b.type === 'text').map((b: { text: string }) => b.text).join(' ')
                    : '';
                preview = text.slice(0, 200);
              }
            }
          } catch { /* skip malformed lines */ }
        }
      } catch { /* skip unreadable files */ }

      sessions.push({
        id,
        title: title || id.slice(0, 8),
        provider: 'claude_code',
        cwd,
        updatedAt: stat.mtimeMs,
        preview,
      });
    }
  } catch { /* dir not readable */ }

  return sessions;
}

/** Читает сессии Qwen Code из ~/.qwen/projects/<slug>/ */
function readQwenSessions(cliHome: string, cwd: string): SessionInfo[] {
  const qwenHome = path.join(cliHome, '.qwen');
  const slug = cwd.replace(/[^a-zA-Z0-9]/g, '-');
  const dir = path.join(qwenHome, 'projects', slug);
  if (!fs.existsSync(dir)) return [];

  const sessions: SessionInfo[] = [];
  try {
    for (const file of fs.readdirSync(dir).filter((f) => f.endsWith('.jsonl'))) {
      const fp = path.join(dir, file);
      const stat = fs.statSync(fp);
      const id = file.replace('.jsonl', '');

      let title = '';
      let preview = '';
      try {
        const content = fs.readFileSync(fp, 'utf-8');
        const lines = content.split('\n').filter(Boolean);
        for (const line of lines.slice(0, 10)) {
          try {
            const obj = JSON.parse(line);
            if (obj.type === 'user' && !title) {
              const msg = obj.message;
              if (msg?.content) {
                const text = typeof msg.content === 'string'
                  ? msg.content
                  : Array.isArray(msg.content)
                    ? msg.content.filter((b: { type: string }) => b.type === 'text').map((b: { text: string }) => b.text).join(' ')
                    : '';
                title = text.slice(0, 80);
              }
            }
            if (obj.type === 'assistant' && !preview) {
              const msg = obj.message;
              if (msg?.content) {
                const text = typeof msg.content === 'string'
                  ? msg.content
                  : Array.isArray(msg.content)
                    ? msg.content.filter((b: { type: string }) => b.type === 'text').map((b: { text: string }) => b.text).join(' ')
                    : '';
                preview = text.slice(0, 200);
              }
            }
          } catch { /* skip */ }
        }
      } catch { /* skip */ }

      sessions.push({
        id,
        title: title || id.slice(0, 8),
        provider: 'qwen_code',
        cwd,
        updatedAt: stat.mtimeMs,
        preview,
      });
    }
  } catch { /* skip */ }

  return sessions;
}

/** Читает сессии Codex из ~/.codex/history.jsonl */
function readCodexSessions(cliHome: string): SessionInfo[] {
  const historyFile = path.join(cliHome, 'history.jsonl');
  if (!fs.existsSync(historyFile)) return [];

  const sessions: SessionInfo[] = [];
  try {
    const content = fs.readFileSync(historyFile, 'utf-8');
    const lines = content.split('\n').filter(Boolean);
    for (const line of lines) {
      try {
        const obj = JSON.parse(line);
        if (obj.session_id) {
          sessions.push({
            id: obj.session_id,
            title: (obj.prompt || obj.session_id).slice(0, 80),
            provider: 'codex',
            cwd: obj.cwd || '',
            updatedAt: obj.timestamp ? obj.timestamp * 1000 : 0,
            preview: (obj.response || '').slice(0, 200),
          });
        }
      } catch { /* skip */ }
    }
  } catch { /* skip */ }

  return sessions;
}

export function listSessions(provider: string, cliHome: string, cwd: string): SessionInfo[] {
  let sessions: SessionInfo[] = [];

  switch (provider) {
    case 'claude_code':
      sessions = readClaudeSessions(cliHome, cwd);
      break;
    case 'qwen_code':
      sessions = readQwenSessions(cliHome, cwd);
      break;
    case 'codex':
      sessions = readCodexSessions(cliHome);
      break;
    default:
      break;
  }

  return sessions.sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 20);
}

export function formatSessionAge(ms: number): string {
  const secs = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (secs < 60) return 'только что';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} мин назад`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} ч назад`;
  return `${Math.floor(hours / 24)} дн назад`;
}