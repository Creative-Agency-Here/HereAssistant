import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import React from 'react';
import { render } from 'ink';
import { ChatInput } from './ChatInput.js';

type TestInput = PassThrough & {
  isTTY: boolean;
  isRaw: boolean;
  setRawMode: (mode: boolean) => TestInput;
  ref: () => TestInput;
  unref: () => TestInput;
};

type TestOutput = PassThrough & {
  columns: number;
  rows: number;
  isTTY: boolean;
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

function makeOutput(): TestOutput {
  const output = new PassThrough() as TestOutput;
  output.columns = 100;
  output.rows = 30;
  output.isTTY = true;
  return output;
}

function delay(ms = 20): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function mountInput() {
  const stdin = makeInput();
  const stdout = makeOutput();
  const stderr = makeOutput();
  const keys = new EventEmitter();
  const mouse = new EventEmitter();
  const submitted: string[] = [];
  const copied: string[] = [];
  let errors = '';
  let renderedOutput = '';
  stdout.on('data', (chunk: Buffer) => { renderedOutput += chunk.toString(); });
  let renderError: unknown;
  stderr.on('data', (chunk: Buffer) => { errors += chunk.toString(); });
  (globalThis as Record<string, unknown>).__ha_keys = keys;
  (globalThis as Record<string, unknown>).__ha_mouse = mouse;

  const instance = render(
    <ChatInput
      onSubmit={(value) => submitted.push(value)}
      onSelectionCopy={(value) => { copied.push(value); return true; }}
    />,
    {
      stdin: stdin as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream,
      stderr: stderr as unknown as NodeJS.WriteStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  void instance.waitUntilExit().catch((error: unknown) => { renderError = error; });
  await delay();

  return {
    stdin,
    keys,
    mouse,
    submitted,
    copied,
    errors: () => errors,
    renderError: () => renderError,
    output: () => renderedOutput,
    async close() {
      instance.unmount();
      stdin.end();
      stdout.end();
      stderr.end();
      delete (globalThis as Record<string, unknown>).__ha_keys;
      delete (globalThis as Record<string, unknown>).__ha_mouse;
      await delay();
    },
  };
}

test('стрелка влево меняет позицию вставки', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('ab');
    await delay();
    app.keys.emit('arrow-key', { direction: 'left', modifiers: 1 });
    await delay();
    app.stdin.write('X');
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.submitted, ['aXb']);
  } finally {
    await app.close();
  }
});

test('Cmd+Left и Cmd+Right переходят в начало и конец строки', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('abc');
    await delay();
    app.keys.emit('arrow-key', { direction: 'left', modifiers: 9 });
    app.stdin.write('X');
    await delay();
    app.keys.emit('arrow-key', { direction: 'right', modifiers: 9 });
    app.stdin.write('Y');
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.submitted, ['XabcY']);
  } finally {
    await app.close();
  }
});

test('Esc очищает непустой черновик', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('старый текст');
    await delay();
    app.keys.emit('escape-key');
    await delay();
    app.stdin.write('новый');
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.submitted, ['новый']);
  } finally {
    await app.close();
  }
});

test('Shift+Esc не очищает черновик', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('оставить');
    await delay();
    app.keys.emit('escape-key', { modifiers: 2 });
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.submitted, ['оставить']);
  } finally {
    await app.close();
  }
});

test('ESC+Enter сохраняет Alt+Enter как перенос строки', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('a');
    await delay();
    app.keys.emit('newline-key', { modifiers: 3 });
    await delay();
    app.stdin.write('b');
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.submitted, ['a\nb']);
  } finally {
    await app.close();
  }
});

test('удаление второй строки возвращает видимый курсор в первую', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('a');
    await delay();
    app.keys.emit('newline-key', { modifiers: 2 });
    await delay();
    const outputBeforeDelete = app.output().length;
    app.stdin.write('\x7f');
    await delay();
    const deleteFrame = app.output()
      .slice(outputBeforeDelete)
      .replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '');
    assert.match(deleteFrame, /a▌/);
    app.stdin.write('b');
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.submitted, ['ab']);
  } finally {
    await app.close();
  }
});

test('клик в строке ввода немедленно переставляет курсор', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('abcd');
    await delay();
    // В изолированном ChatInput текст начинается с terminal column 4.
    // Колонка 6 соответствует позиции между "b" и "c".
    app.mouse.emit('event', { type: 'press', button: 'left', col: 6, row: 1 });
    await delay();
    app.stdin.write('X');
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.deepEqual(app.submitted, ['abXcd']);
  } finally {
    await app.close();
  }
});

test('drag выделяет текст, а Cmd+C копирует его явно', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('abcd');
    await delay();
    // Текст начинается с terminal column 4: drag от позиции 1 до позиции 3.
    app.mouse.emit('event', { type: 'press', button: 'left', col: 5, row: 1 });
    app.mouse.emit('event', { type: 'move', button: 'left', col: 7, row: 1 });
    app.mouse.emit('event', { type: 'release', button: 'left', col: 7, row: 1 });
    await delay();
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.copied, []);

    const copySelection = (globalThis as Record<string, unknown>).__ha_input_copy_selection as
      | (() => boolean)
      | undefined;
    assert.equal(copySelection?.(), true);
    await delay();
    assert.deepEqual(app.copied, ['bc']);
  } finally {
    await app.close();
  }
});

test('двойной Enter создаёт перенос и не отправляет первый Enter', async () => {
  const app = await mountInput();
  try {
    app.stdin.write('a');
    await delay();
    app.stdin.write('\r');
    await delay(10);
    app.stdin.write('\r');
    await delay();
    assert.deepEqual(app.submitted, []);

    app.stdin.write('b');
    await delay();
    app.stdin.write('\r');
    await delay(80);
    assert.equal(app.renderError(), undefined);
    assert.equal(app.errors(), '');
    assert.deepEqual(app.submitted, ['a\nb']);
  } finally {
    await app.close();
  }
});
