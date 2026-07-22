import type { Account } from './types.js';
import { getAccounts } from './db.js';
import { pasteImageFromClipboard } from './clipboard.js';
import { listSessions, formatSessionAge } from './sessions.js';
import { THEME_NAMES } from './themes.js';
import { loadMcpConfig, addMcpServer, removeMcpServer, formatMcpServers, type McpServer } from './mcp.js';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export interface CommandContext {
  account: Account;
  model: string;
  sessionId: string | null;
  cwd: string;
  tokensIn: number;
  tokensOut: number;
  setModel: (m: string) => void;
  setAccount: (a: Account) => void;
  resetSession: () => void;
  setSessionId: (id: string) => void;
  renameSession: (name: string) => void;
  setTheme: (name: string) => void;
  forkSession: () => void;
  backgroundPrompt: (prompt: string) => void;
  voiceInput: (text: string) => void;
  togglePlain: () => void;
  print: (text: string) => void;
  exit: () => void;
  attachImage: (path: string) => void;
}

const HELP = `Команды:
  /help              эта справка
  /model [имя]       показать/сменить модель
  /account [label]   показать/сменить аккаунт
  /status            сессия, модель, токены
  /resume [id]       продолжить сессию (без id — список)
  /rename <имя>      переименовать текущую сессию
  /fork              форк сессии (копия контекста, новый ID)
  /search <query>    веб-поиск (через провайдер)
  /bg <prompt>       фоновый агент (detach)
  /theme [имя]       тема (dark/light/mono/neon)
  /archive [id]      архивировать сессию
  /delete [id]       удалить сессию
  /mcp [list|add|rm] управление MCP-серверами
  /plain             режим без ANSI (для копирования)
  /image             вставить фото из clipboard (Ctrl+V)
  /diff              показать git diff
  /new               новая сессия (очистить контекст)
  /compact           сжать контекст (заглушка)
  /exit              выход

Ввод:
  Enter              отправить
  Alt+Enter          новая строка
  ↑↓                 история / навигация
  Tab                автодополнение /команд и @файлов
  Ctrl+V             вставить фото из clipboard
  Ctrl+G             внешний редактор ($EDITOR)
  !команда           выполнить shell-команду
  Ctrl+U/W/K         очистить строку / слово / до конца`;

export function handleCommand(line: string, ctx: CommandContext): boolean {
  const parts = line.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();
  const arg = parts.slice(1).join(' ');

  switch (cmd) {
    case '/help':
      ctx.print(HELP);
      return true;

    case '/model':
      if (arg) {
        ctx.setModel(arg);
        ctx.resetSession();
        ctx.print(`▸ модель: ${arg}`);
      } else {
        ctx.print(`▸ модель: ${ctx.model || ctx.account.default_model || 'default'}`);
      }
      return true;

    case '/account': {
      if (arg) {
        const accounts = getAccounts();
        const found = accounts.find((a) => a.label === arg);
        if (found) {
          ctx.setAccount(found);
          ctx.resetSession();
          ctx.print(`▸ аккаунт: ${found.label} (${found.provider})`);
        } else {
          ctx.print(`✗ аккаунт "${arg}" не найден`);
        }
      } else {
        const accounts = getAccounts();
        const list = accounts.map((a) =>
          `  ${a.label === ctx.account.label ? '❯' : ' '} ${a.label} · ${a.provider} · ${a.default_model || 'default'}`,
        ).join('\n');
        ctx.print(`Аккаунты:\n${list}`);
      }
      return true;
    }

    case '/status': {
      const tokens = ctx.tokensIn + ctx.tokensOut;
      const mcp = loadMcpConfig(ctx.cwd);
      ctx.print(
        `▸ аккаунт: ${ctx.account.label} (${ctx.account.provider})\n` +
        `▸ модель: ${ctx.model || ctx.account.default_model || 'default'}\n` +
        `▸ сессия: ${ctx.sessionId ? ctx.sessionId.slice(0, 16) : 'нет'}\n` +
        `▸ токены: ${tokens > 0 ? (tokens / 1000).toFixed(1) + 'k' : '0'}\n` +
        `▸ проект: ${ctx.cwd}\n` +
        `▸ MCP: ${mcp.servers.length} серверов\n${formatMcpServers(mcp)}`,
      );
      return true;
    }

    case '/new':
      ctx.resetSession();
      ctx.print('▸ новая сессия — контекст очищен');
      return true;

    case '/resume': {
      if (arg) {
        ctx.setSessionId(arg);
        ctx.print(`▸ продолжаю сессию ${arg.slice(0, 16)}`);
      } else {
        const sessions = listSessions(ctx.account.provider, ctx.account.cli_home_path, ctx.cwd);
        if (sessions.length === 0) {
          ctx.print('▸ нет прошлых сессий для этого провайдера');
        } else {
          const list = sessions.slice(0, 10).map((s, i) =>
            `  ${i + 1}. ${s.title}\n     ${formatSessionAge(s.updatedAt)} · ${s.id.slice(0, 12)}`,
          ).join('\n');
          ctx.print(`Прошлые сессии (${ctx.account.provider}):\n${list}\n\n/resume <id> — продолжить`);
        }
      }
      return true;
    }

    case '/diff': {
      try {
        const stat = execSync('git diff --stat HEAD 2>/dev/null', {
          cwd: ctx.cwd, encoding: 'utf-8', timeout: 5000,
        }).trim();
        if (!stat) { ctx.print('▸ нет изменений'); return true; }
        const files = stat.split('\n').filter((l) => l.includes('|'));
        const summary = stat.split('\n').pop() || '';
        let output = `┌─ 📝 DIFF ${'─'.repeat(50)}┐\n`;
        for (const f of files) {
          const match = f.match(/^\s*(.+?)\s*\|\s*(\d+)\s*([+-]*)/);
          if (match) {
            const name = match[1].trim();
            const bars = match[3];
            const added = (bars.match(/\+/g) || []).length;
            const removed = (bars.match(/-/g) || []).length;
            output += `│ \x1b[36m${name}\x1b[0m  \x1b[32m+${added}\x1b[0m/\x1b[31m-${removed}\x1b[0m ${bars}\n`;
          }
        }
        output += `├${'─'.repeat(58)}┤\n│ ${summary}\n└${'─'.repeat(58)}┘`;
        const fullDiff = execSync('git diff HEAD 2>/dev/null | head -60', {
          cwd: ctx.cwd, encoding: 'utf-8', timeout: 5000,
        }).trim();
        if (fullDiff) {
          output += '\n\n' + fullDiff.split('\n').map((l) => {
            if (l.startsWith('+') && !l.startsWith('+++')) return `\x1b[32m${l}\x1b[0m`;
            if (l.startsWith('-') && !l.startsWith('---')) return `\x1b[31m${l}\x1b[0m`;
            if (l.startsWith('@@')) return `\x1b[36m${l}\x1b[0m`;
            return l;
          }).join('\n');
        }
        ctx.print(output);
      } catch {
        ctx.print('✗ git diff недоступен');
      }
      return true;
    }

    case '/compact':
      ctx.print('▸ /compact: провайдер сам управляет контекстом (заглушка)');
      return true;

    case '/fork':
      ctx.forkSession();
      ctx.print('▸ сессия форкнута — новый ID, контекст сохранён');
      return true;

    case '/rename': {
      if (!arg) { ctx.print('Использование: /rename <имя>'); return true; }
      ctx.renameSession(arg);
      ctx.print(`▸ сессия переименована: ${arg}`);
      return true;
    }

    case '/search': {
      if (!arg) { ctx.print('Использование: /search <запрос>'); return true; }
      ctx.print(`🔍 поиск: ${arg}\n(отправлено провайдеру как промпт с web search)`);
      // Search is handled by passing the query as a special prompt
      // The provider will use its web search capability
      return false; // Let it fall through to be sent as a prompt
    }

    case '/bg': {
      if (!arg) { ctx.print('Использование: /bg <промпт>'); return true; }
      ctx.backgroundPrompt(arg);
      return true;
    }

    case '/theme': {
      if (!arg) {
        ctx.print(`Темы: ${THEME_NAMES.join(', ')}\n/theme <имя> — переключить`);
      } else if (THEME_NAMES.includes(arg)) {
        ctx.setTheme(arg);
        ctx.print(`▸ тема: ${arg}`);
      } else {
        ctx.print(`✗ неизвестная тема "${arg}". Доступные: ${THEME_NAMES.join(', ')}`);
      }
      return true;
    }

    case '/archive': {
      const sid = arg || ctx.sessionId;
      if (!sid) { ctx.print('✗ нет активной сессии'); return true; }
      const slug = ctx.cwd.replace(/[^a-zA-Z0-9]/g, '-');
      const src = path.join(ctx.account.cli_home_path, 'projects', slug, `${sid}.jsonl`);
      const archiveDir = path.join(ctx.account.cli_home_path, 'projects', slug, '.archive');
      if (fs.existsSync(src)) {
        fs.mkdirSync(archiveDir, { recursive: true });
        fs.renameSync(src, path.join(archiveDir, `${sid}.jsonl`));
        ctx.print(`▸ сессия ${sid.slice(0, 12)} архивирована`);
      } else {
        ctx.print(`✗ файл сессии не найден`);
      }
      return true;
    }

    case '/delete': {
      const sid = arg || ctx.sessionId;
      if (!sid) { ctx.print('✗ нет активной сессии'); return true; }
      const slug = ctx.cwd.replace(/[^a-zA-Z0-9]/g, '-');
      const fp = path.join(ctx.account.cli_home_path, 'projects', slug, `${sid}.jsonl`);
      if (fs.existsSync(fp)) {
        fs.unlinkSync(fp);
        ctx.print(`▸ сессия ${sid.slice(0, 12)} удалена`);
      } else {
        ctx.print(`✗ файл сессии не найден`);
      }
      return true;
    }

    case '/mcp': {
      const mcpConfig = loadMcpConfig(ctx.cwd);
      const sub = parts[1]?.toLowerCase();
      if (!sub || sub === 'list') {
        ctx.print(`MCP-серверы (${mcpConfig.servers.length}):\n${formatMcpServers(mcpConfig)}`);
      } else if (sub === 'add' && parts[2]) {
        const name = parts[2];
        const url = parts[3];
        if (url) {
          addMcpServer(ctx.cwd, { name, httpUrl: url, description: parts.slice(4).join(' ') || undefined });
          ctx.print(`▸ MCP-сервер добавлен: ${name} → ${url}`);
        } else {
          ctx.print('Использование: /mcp add <имя> <url> [описание]');
        }
      } else if ((sub === 'rm' || sub === 'remove') && parts[2]) {
        if (removeMcpServer(ctx.cwd, parts[2])) {
          ctx.print(`▸ MCP-сервер удалён: ${parts[2]}`);
        } else {
          ctx.print(`✗ сервер "${parts[2]}" не найден`);
        }
      } else {
        ctx.print('Использование: /mcp [list|add <имя> <url>|rm <имя>]');
      }
      return true;
    }

    case '/plain':
      ctx.togglePlain();
      return true;

    case '/image': {
      const imgPath = pasteImageFromClipboard();
      if (imgPath) {
        ctx.attachImage(imgPath);
        ctx.print(`📎 изображение прикреплено: ${imgPath.split('/').pop()}`);
      } else {
        ctx.print('✗ в clipboard нет изображения (скопируй фото через Cmd+C)');
      }
      return true;
    }

    case '/exit':
    case '/quit':
      ctx.exit();
      return true;

    default:
      return false;
  }
}