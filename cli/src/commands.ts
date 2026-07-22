import type { Account } from './types.js';
import { getAccounts } from './db.js';
import { pasteImageFromClipboard } from './clipboard.js';

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
  print: (text: string) => void;
  exit: () => void;
  attachImage: (path: string) => void;
}

const HELP = `Команды:
  /help              эта справка
  /model [имя]       показать/сменить модель
  /account [label]   показать/сменить аккаунт
  /status            сессия, модель, токены
  /image             вставить фото из clipboard (Cmd+V)
  /new               новая сессия (очистить контекст)
  /compact           сжать контекст (заглушка)
  /exit              выход

Ввод:
  Enter              отправить
  Alt+Enter          новая строка
  ↑↓                 история / навигация по строкам
  Tab                автодополнение команд
  Ctrl+U             очистить строку
  Ctrl+W             удалить слово
  Ctrl+C             выход`;

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
      ctx.print(
        `▸ аккаунт: ${ctx.account.label} (${ctx.account.provider})\n` +
        `▸ модель: ${ctx.model || ctx.account.default_model || 'default'}\n` +
        `▸ сессия: ${ctx.sessionId ? ctx.sessionId.slice(0, 16) : 'нет'}\n` +
        `▸ токены: ${tokens > 0 ? (tokens / 1000).toFixed(1) + 'k' : '0'}\n` +
        `▸ проект: ${ctx.cwd}`,
      );
      return true;
    }

    case '/new':
      ctx.resetSession();
      ctx.print('▸ новая сессия — контекст очищен');
      return true;

    case '/compact':
      ctx.print('▸ /compact: провайдер сам управляет контекстом (заглушка)');
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