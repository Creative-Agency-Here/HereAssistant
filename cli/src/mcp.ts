import fs from 'node:fs';
import path from 'node:path';

export interface McpServer {
  name: string;
  command?: string;
  args?: string[];
  httpUrl?: string;
  headers?: Record<string, string>;
  env?: Record<string, string>;
  description?: string;
  trust?: boolean;
}

export interface McpConfig {
  servers: McpServer[];
}

const CONFIG_PATHS = [
  '.hereassistant/mcp.json',
  '.qwen/settings.json',
  '.mcp.json',
];

/** Читает MCP-конфиг из проекта (приоритет: .hereassistant > .qwen > .mcp.json). */
export function loadMcpConfig(cwd: string): McpConfig {
  for (const name of CONFIG_PATHS) {
    const fp = path.join(cwd, name);
    if (!fs.existsSync(fp)) continue;
    try {
      const raw = JSON.parse(fs.readFileSync(fp, 'utf-8'));

      // .hereassistant/mcp.json — наш формат
      if (name === '.hereassistant/mcp.json') {
        const servers = raw.servers || raw.mcpServers || {};
        return { servers: normalizeServers(servers) };
      }

      // .qwen/settings.json — Qwen формат
      if (name === '.qwen/settings.json') {
        const servers = raw.mcpServers || {};
        return { servers: normalizeServers(servers) };
      }

      // .mcp.json — Claude Code формат
      if (name === '.mcp.json') {
        const servers = raw.mcpServers || raw.servers || {};
        return { servers: normalizeServers(servers) };
      }
    } catch { /* skip malformed */ }
  }
  return { servers: [] };
}

function normalizeServers(raw: Record<string, unknown>): McpServer[] {
  return Object.entries(raw).map(([name, cfg]) => {
    const c = cfg as Record<string, unknown>;
    return {
      name,
      command: c.command as string | undefined,
      args: c.args as string[] | undefined,
      httpUrl: (c.httpUrl || c.url) as string | undefined,
      headers: c.headers as Record<string, string> | undefined,
      env: c.env as Record<string, string> | undefined,
      description: c.description as string | undefined,
      trust: c.trust as boolean | undefined,
    };
  });
}

/** Сохраняет MCP-конфиг в .hereassistant/mcp.json. */
export function saveMcpConfig(cwd: string, config: McpConfig): void {
  const dir = path.join(cwd, '.hereassistant');
  fs.mkdirSync(dir, { recursive: true });
  const servers: Record<string, unknown> = {};
  for (const s of config.servers) {
    const entry: Record<string, unknown> = {};
    if (s.command) entry.command = s.command;
    if (s.args) entry.args = s.args;
    if (s.httpUrl) entry.httpUrl = s.httpUrl;
    if (s.headers) entry.headers = s.headers;
    if (s.env) entry.env = s.env;
    if (s.description) entry.description = s.description;
    servers[s.name] = entry;
  }
  fs.writeFileSync(
    path.join(dir, 'mcp.json'),
    JSON.stringify({ servers }, null, 2) + '\n',
    'utf-8',
  );
}

/** Добавляет MCP-сервер. */
export function addMcpServer(cwd: string, server: McpServer): void {
  const config = loadMcpConfig(cwd);
  const idx = config.servers.findIndex((s) => s.name === server.name);
  if (idx >= 0) config.servers[idx] = server;
  else config.servers.push(server);
  saveMcpConfig(cwd, config);
}

/** Удаляет MCP-сервер. */
export function removeMcpServer(cwd: string, name: string): boolean {
  const config = loadMcpConfig(cwd);
  const before = config.servers.length;
  config.servers = config.servers.filter((s) => s.name !== name);
  if (config.servers.length < before) {
    saveMcpConfig(cwd, config);
    return true;
  }
  return false;
}

/** Генерирует временный .mcp.json для Claude Code (--mcp-config). */
export function generateClaudeMcpFile(cwd: string): string | null {
  const config = loadMcpConfig(cwd);
  if (config.servers.length === 0) return null;

  const mcpServers: Record<string, unknown> = {};
  for (const s of config.servers) {
    if (s.command) {
      mcpServers[s.name] = { command: s.command, args: s.args || [] };
    } else if (s.httpUrl) {
      mcpServers[s.name] = { url: s.httpUrl, headers: s.headers || {} };
    }
  }

  const tmpPath = path.join(cwd, '.hereassistant', '.mcp-generated.json');
  fs.writeFileSync(tmpPath, JSON.stringify({ mcpServers }, null, 2), 'utf-8');
  return tmpPath;
}

/** Форматирует список серверов для вывода. */
export function formatMcpServers(config: McpConfig): string {
  if (config.servers.length === 0) return '  (нет MCP-серверов)';
  return config.servers.map((s) => {
    const type = s.httpUrl ? 'HTTP' : s.command ? 'stdio' : '?';
    const target = s.httpUrl || s.command || '?';
    const desc = s.description ? ` — ${s.description}` : '';
    return `  ${s.name} [${type}] ${target}${desc}`;
  }).join('\n');
}