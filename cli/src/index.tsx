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
const stdinWasRaw = Boolean(process.stdin.isRaw);
if (process.stdin.isTTY) process.stdin.setRawMode?.(true);

(globalThis as any).__ha_mouse = filter.mouse;
(globalThis as any).__ha_voice = filter.voice;
(globalThis as any).__ha_keys = filter.keys;
(globalThis as any).__ha_filter = filter;

if (process.stdout.isTTY) {
  process.stdout.write('\x1b[?1049h');
  // Те же mouse modes, которые включает Claude Code: click, drag, hover и SGR coordinates.
  process.stdout.write('\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h');
  process.stdout.write('\x1b[?2004h'); // bracketed paste mode
  process.stdout.write('\x1b[>1u'); // Kitty keyboard protocol (disambiguate)
  process.stdout.write('\x1b[2J\x1b[H');
}

let cleanedUp = false;
const cleanup = () => {
  if (cleanedUp) return;
  cleanedUp = true;
  if (process.stdout.isTTY) {
    process.stdout.write('\x1b[<u'); // Kitty keyboard protocol off
    process.stdout.write('\x1b[?2004l'); // bracketed paste off
    process.stdout.write('\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l');
    process.stdout.write('\x1b[?1049l');
  }
  if (process.stdin.isTTY) process.stdin.setRawMode?.(stdinWasRaw);
};
process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(0); });
process.on('SIGTERM', () => { cleanup(); process.exit(0); });

const { waitUntilExit } = render(
  <App preselected={preselected} resumeId={resumeId} integrationId={integrationId} />,
  { stdin: filter as any, exitOnCtrlC: false },
);

waitUntilExit().then(cleanup);
