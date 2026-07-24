import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export interface HaSessionMessage {
  role: 'user' | 'assistant' | 'system';
  text: string;
  timestamp: number;
  provider?: string;
  model?: string;
  attachments?: string[];
}

export interface HaSession {
  id: string;
  name: string | null;
  createdAt: number;
  updatedAt: number;
  cwd: string;
  messages: HaSessionMessage[];
}

const SESSIONS_DIR = path.join(os.homedir(), '.hereassistant', 'sessions');

function ensureDir(): void {
  fs.mkdirSync(SESSIONS_DIR, { recursive: true });
}

function sessionPath(id: string): string {
  return path.join(SESSIONS_DIR, `${id}.json`);
}

export function createHaSession(cwd: string): HaSession {
  ensureDir();
  const id = `ha-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const session: HaSession = {
    id,
    name: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    cwd,
    messages: [],
  };
  fs.writeFileSync(sessionPath(id), JSON.stringify(session));
  return session;
}

export function loadHaSession(id: string): HaSession | null {
  const fp = sessionPath(id);
  if (!fs.existsSync(fp)) return null;
  try {
    return JSON.parse(fs.readFileSync(fp, 'utf-8'));
  } catch {
    return null;
  }
}

export function saveHaSession(session: HaSession): void {
  ensureDir();
  session.updatedAt = Date.now();
  fs.writeFileSync(sessionPath(session.id), JSON.stringify(session));
}

export function listHaSessions(cwd?: string): HaSession[] {
  ensureDir();
  const sessions: HaSession[] = [];
  try {
    for (const file of fs.readdirSync(SESSIONS_DIR).filter((f) => f.endsWith('.json'))) {
      try {
        const session: HaSession = JSON.parse(fs.readFileSync(path.join(SESSIONS_DIR, file), 'utf-8'));
        if (!cwd || session.cwd === cwd) sessions.push(session);
      } catch { /* skip corrupted */ }
    }
  } catch { /* dir not readable */ }
  return sessions.sort((a, b) => b.updatedAt - a.updatedAt);
}

export function deleteHaSession(id: string): boolean {
  const fp = sessionPath(id);
  if (!fs.existsSync(fp)) return false;
  fs.unlinkSync(fp);
  return true;
}

export function haSessionTitle(s: HaSession): string {
  if (s.name) return s.name;
  const firstUser = s.messages.find((m) => m.role === 'user');
  return firstUser ? firstUser.text.slice(0, 80) : s.id;
}

/** Форматирует историю для включения в промпт провайдеру. */
export function formatHistoryForPrompt(messages: HaSessionMessage[], maxMessages = 30): string {
  const recent = messages.filter((m) => m.role === 'user' || m.role === 'assistant').slice(-maxMessages);
  if (recent.length === 0) return '';

  const lines: string[] = ['=== История разговора (контекст предыдущих сообщений) ==='];
  for (const msg of recent) {
    const label = msg.role === 'user' ? 'Пользователь' : 'Ассистент';
    const limit = msg.role === 'user' ? 2000 : 3000;
    lines.push(`[${label}]: ${msg.text.slice(0, limit)}`);
  }
  lines.push('=== Конец истории ===');
  return lines.join('\n');
}
