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

const filter = new MouseFilterStream();
process.stdin.pipe(filter);

(globalThis as any).__ha_mouse = filter.mouse;
(globalThis as any).__ha_voice = filter.voice;
(globalThis as any).__ha_filter = filter;

if (process.stdout.isTTY) {
  process.stdout.write('\x1b[?1049h'); // alternate screen
  process.stdout.write('\x1b[?1000h\x1b[?1006h'); // mouse reporting для кликов
  process.stdout.write('\x1b[2J\x1b[H');
}

const cleanup = () => {
  if (process.stdout.isTTY) {
    process.stdout.write('\x1b[?1000l\x1b[?1006l');
    process.stdout.write('\x1b[?1049l');
  }
};
process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(0); });
process.on('SIGTERM', () => { cleanup(); process.exit(0); });

const { waitUntilExit } = render(
  <App preselected={preselected} resumeId={resumeId} integrationId={integrationId} />,
  { stdin: filter as any },
);

waitUntilExit().then(cleanup);