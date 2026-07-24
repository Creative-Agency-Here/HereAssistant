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
const SESSION_ID_PATTERN = /^ha-[a-z0-9]+-[a-z0-9]+$/;

function ensureDir(): void {
  fs.mkdirSync(SESSIONS_DIR, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(SESSIONS_DIR, 0o700); } catch { /* Windows или read-only FS */ }
}

export function isValidHaSessionId(id: string): boolean {
  return SESSION_ID_PATTERN.test(id);
}

function sessionPath(id: string): string | null {
  if (!isValidHaSessionId(id)) return null;
  return path.join(SESSIONS_DIR, `${id}.json`);
}

function isHaSession(value: unknown, expectedId?: string): value is HaSession {
  if (!value || typeof value !== 'object') return false;
  const session = value as Partial<HaSession>;
  const messages = session.messages;
  return typeof session.id === 'string'
    && isValidHaSessionId(session.id)
    && (!expectedId || session.id === expectedId)
    && (session.name === null || typeof session.name === 'string')
    && typeof session.createdAt === 'number'
    && typeof session.updatedAt === 'number'
    && typeof session.cwd === 'string'
    && Array.isArray(messages)
    && messages.every((message) => (
      Boolean(message)
      && typeof message === 'object'
      && ['user', 'assistant', 'system'].includes(message.role)
      && typeof message.text === 'string'
      && typeof message.timestamp === 'number'
      && (
        typeof message.attachments === 'undefined'
        || (
          Array.isArray(message.attachments)
          && message.attachments.every((attachment) => typeof attachment === 'string')
        )
      )
    ));
}

function writeSession(session: HaSession): void {
  ensureDir();
  const target = sessionPath(session.id);
  if (!target) throw new Error('Некорректный идентификатор HA-сессии');
  const temporary = `${target}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
  try {
    fs.writeFileSync(temporary, JSON.stringify(session), {
      encoding: 'utf-8',
      flag: 'wx',
      mode: 0o600,
    });
    fs.renameSync(temporary, target);
    try { fs.chmodSync(target, 0o600); } catch { /* Windows или read-only FS */ }
  } finally {
    try {
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    } catch { /* cleanup best effort */ }
  }
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
  writeSession(session);
  return session;
}

export function loadHaSession(id: string): HaSession | null {
  const fp = sessionPath(id);
  if (!fp || !fs.existsSync(fp)) return null;
  try {
    const session: unknown = JSON.parse(fs.readFileSync(fp, 'utf-8'));
    return isHaSession(session, id) ? session : null;
  } catch {
    return null;
  }
}

export function saveHaSession(session: HaSession): void {
  session.updatedAt = Date.now();
  writeSession(session);
}

export function listHaSessions(cwd?: string): HaSession[] {
  ensureDir();
  const sessions: HaSession[] = [];
  try {
    for (const file of fs.readdirSync(SESSIONS_DIR).filter((f) => f.endsWith('.json'))) {
      try {
        const expectedId = file.slice(0, -'.json'.length);
        const parsed: unknown = JSON.parse(fs.readFileSync(path.join(SESSIONS_DIR, file), 'utf-8'));
        if (!isHaSession(parsed, expectedId)) continue;
        const session = parsed;
        if (!cwd || session.cwd === cwd) sessions.push(session);
      } catch { /* skip corrupted */ }
    }
  } catch { /* dir not readable */ }
  return sessions.sort((a, b) => b.updatedAt - a.updatedAt);
}

export function deleteHaSession(id: string): boolean {
  const fp = sessionPath(id);
  if (!fp || !fs.existsSync(fp)) return false;
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
