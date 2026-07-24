import type { Account } from './types.js';
import { getAccounts } from './db.js';
import { pasteImageFromClipboard } from './clipboard.js';
import { listHaSessions, deleteHaSession, haSessionTitle, type HaSession } from './ha-sessions.js';
import { THEME_NAMES } from './themes.js';
import { loadMcpConfig, addMcpServer, removeMcpServer, formatMcpServers, type McpServer } from './mcp.js';
import { execSync } from 'node:child_process';

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
  resumeSession: (id: string) => void;
  openSessionPicker: (sessions: HaSession[]) => void;
  renameSession: (name: string) => void;
  forkSession: () => void;
  setTheme: (name: string) => void;
  backgroundPrompt: (prompt: string) => void;
  voiceInput: (text: string) => void;
  togglePlain: () => void;
  copyLast: () => void;
  insertAtCursor: (text: string) => void;
  print: (text: string) => void;
  exit: () => void;
  attachImage: (path: string) => void;
}

let lastSessionList: HaSession[] = [];

const HELP = `Команды:
  /help              эта справка
  /model [имя]       показать/сменить модель
  /account [label]   показать/сменить аккаунт
  /status            сессия, модель, токены
  /resume [id|номер] продолжить сессию (без аргумента — список)
  /rename <имя>      переименовать текущую сессию
  /fork              форк сессии (копия контекста, новый ID)
  /search <query>    веб-поиск (через провайдер)
  /bg <prompt>       фоновый агент (detach)
  /theme [имя]       тема (dark/light/mono/neon)
  /archive [id]      архивировать сессию
  /delete [id]       удалить сессию
  /mcp [list|add|rm] управление MCP-серверами
  /copy              скопировать последний ответ в clipboard
  /img               вставить фото из clipboard в текст
  /nl                новая строка (если Alt+Enter не работает)
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
        `▸ сессия HA: ${ctx.sessionId ? ctx.sessionId.slice(0, 20) : 'нет'}\n` +
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
        const num = parseInt(arg, 10);
        if (!isNaN(num) && num > 0 && num <= lastSessionList.length) {
          const session = lastSessionList[num - 1];
          ctx.resumeSession(session.id);
          ctx.print(`▸ продолжаю сессию: ${haSessionTitle(session)}`);
        } else {
          ctx.resumeSession(arg);
          ctx.print(`▸ продолжаю сессию ${arg.slice(0, 16)}`);
        }
      } else {
        const sessions = listHaSessions(ctx.cwd);
        lastSessionList = sessions;
        if (sessions.length === 0) {
          ctx.print('▸ нет прошлых сессий');
        } else {
          ctx.openSessionPicker(sessions);
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
      if (deleteHaSession(sid)) {
        ctx.print(`▸ сессия ${sid.slice(0, 16)} архивирована (удалена из HA)`);
      } else {
        ctx.print(`✗ сессия не найдена в хранилище HA`);
      }
      return true;
    }

    case '/delete': {
      const sid = arg || ctx.sessionId;
      if (!sid) { ctx.print('✗ нет активной сессии'); return true; }
      if (deleteHaSession(sid)) {
        ctx.print(`▸ сессия ${sid.slice(0, 16)} удалена`);
      } else {
        ctx.print(`✗ сессия не найдена в хранилище HA`);
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

    case '/copy':
      ctx.copyLast();
      return true;

    case '/nl':
      ctx.insertAtCursor('\n');
      return true;

    case '/img': {
      const imgPath = pasteImageFromClipboard();
      if (imgPath) {
        ctx.attachImage(imgPath);
        ctx.insertAtCursor(`[Image]`);
      } else {
        ctx.print('✗ в clipboard нет изображения');
      }
      return true;
    }

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