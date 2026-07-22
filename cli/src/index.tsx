#!/usr/bin/env node
import React from 'react';
import { render } from 'ink';
import { App } from './components/App.js';
import { MouseFilterStream } from './mouse-filter.js';

const args = process.argv.slice(2);

function argAfter(flag: string): string | undefined {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : undefined;
}

const preselected = argAfter('-a');
const resumeId = argAfter('--resume');
const profile = argAfter('-p');
const integrationId = argAfter('--integration-id');

if (profile) process.env.HA_PROFILE = profile;

// Фильтрованный stdin: mouse-события перехватываются ДО Ink
const mouseFilter = new MouseFilterStream();
process.stdin.pipe(mouseFilter);

// Экспортируем mouse emitter для useMouse hook
(globalThis as Record<string, unknown>).__ha_mouse = mouseFilter.mouse;

// Включаем mouse reporting
if (process.stdout.isTTY) {
  process.stdout.write('\x1b[?1000h\x1b[?1006h');
  const cleanup = () => { process.stdout.write('\x1b[?1000l\x1b[?1006l'); };
  process.on('exit', cleanup);
  process.on('SIGINT', () => { cleanup(); process.exit(0); });
  process.on('SIGTERM', () => { cleanup(); process.exit(0); });
}

// Ink получает только клавиши (mouse отфильтрованы)
const { waitUntilExit } = render(
  <App preselected={preselected} resumeId={resumeId} integrationId={integrationId} />,
  { stdin: mouseFilter as unknown as NodeJS.ReadStream },
);

waitUntilExit().then(() => {
  if (process.stdout.isTTY) {
    process.stdout.write('\x1b[?1000l\x1b[?1006l');
  }
});