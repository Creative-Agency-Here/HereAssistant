import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import React from 'react';
import { render } from 'ink';
import { SessionPicker } from './SessionPicker.js';
import type { HaSession } from '../ha-sessions.js';

type TestInput = PassThrough & {
  isTTY: boolean;
  isRaw: boolean;
  setRawMode: (mode: boolean) => TestInput;
  ref: () => TestInput;
  unref: () => TestInput;
};

function makeInput(): TestInput {
  const input = new PassThrough() as TestInput;
  input.isTTY = true;
  input.isRaw = false;
  input.setRawMode = (mode: boolean) => {
    input.isRaw = mode;
    return input;
  };
  input.ref = () => input;
  input.unref = () => input;
  return input;
}

function delay(ms = 20): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const SESSION: HaSession = {
  id: 'ha-review-session',
  name: 'Review',
  createdAt: Date.now(),
  updatedAt: Date.now(),
  cwd: '/tmp/project',
  messages: [],
};

async function mountPicker() {
  const stdin = makeInput();
  const stdout = new PassThrough();
  const stderr = new PassThrough();
  const keys = new EventEmitter();
  let cancelled = 0;
  const selected: string[] = [];
  (globalThis as Record<string, unknown>).__ha_keys = keys;

  const instance = render(
    <SessionPicker
      sessions={[SESSION]}
      onSelect={(id) => selected.push(id)}
      onCancel={() => { cancelled++; }}
    />,
    {
      stdin: stdin as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream,
      stderr: stderr as unknown as NodeJS.WriteStream,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  await delay();

  return {
    stdin,
    keys,
    selected,
    cancelled: () => cancelled,
    async close() {
      instance.unmount();
      stdin.end();
      stdout.end();
      stderr.end();
      delete (globalThis as Record<string, unknown>).__ha_keys;
      await delay();
    },
  };
}

test('custom Escape закрывает список сессий', async () => {
  const app = await mountPicker();
  try {
    app.keys.emit('escape-key', { modifiers: 1 });
    await delay();
    assert.equal(app.cancelled(), 1);
  } finally {
    await app.close();
  }
});

test('Escape из превью возвращает в список, а Shift+Escape ничего не меняет', async () => {
  const app = await mountPicker();
  try {
    app.stdin.write('\r');
    await delay();
    app.keys.emit('escape-key', { modifiers: 2 });
    await delay();
    assert.equal(app.cancelled(), 0);
    assert.deepEqual(app.selected, []);

    app.keys.emit('escape-key', { modifiers: 1 });
    await delay();
    assert.equal(app.cancelled(), 0);

    app.keys.emit('escape-key', { modifiers: 1 });
    await delay();
    assert.equal(app.cancelled(), 1);
  } finally {
    await app.close();
  }
});
