import assert from 'node:assert/strict';
import { once } from 'node:events';
import test from 'node:test';
import {
  MouseFilterStream,
  type ParsedMouseEvent,
  type ParsedNavigationKey,
} from './mouse-filter.js';

async function runFilter(
  input: string,
  onFilter?: (filter: MouseFilterStream) => void,
): Promise<{ output: string; filter: MouseFilterStream }> {
  const filter = new MouseFilterStream();
  let output = '';
  filter.on('data', (chunk: Buffer) => { output += chunk.toString(); });
  onFilter?.(filter);
  filter.end(Buffer.from(input));
  await once(filter, 'end');
  return { output, filter };
}

test('парсит CSI, SS3 и Kitty navigation keys в общие события', async () => {
  const events: ParsedNavigationKey[] = [];
  const { output } = await runFilter(
    '\x1b[D\x1b[1;5C\x1bOA\x1b[57353;1u\x1b[H\x1b[57355;1u',
    (filter) => {
      filter.keys.on('arrow-key', (event: ParsedNavigationKey) => events.push(event));
    },
  );

  assert.equal(output, '');
  assert.deepEqual(events, [
    { direction: 'left', modifiers: 1 },
    { direction: 'right', modifiers: 5 },
    { direction: 'up', modifiers: 1 },
    { direction: 'down', modifiers: 1 },
    { direction: 'home', modifiers: 1 },
    { direction: 'end', modifiers: 1 },
  ]);
});

test('различает Option+Delete и Cmd+Delete', async () => {
  const { output } = await runFilter('\x1b[3;3~\x1b[3;2~\x1b[27;2;127~');
  assert.equal(output, '\x17\x15\x15');
});

test('различает mouse press, move и release', async () => {
  const events: ParsedMouseEvent[] = [];
  const { output } = await runFilter(
    '\x1b[<0;4;2M\x1b[<32;6;2M\x1b[<0;6;2m\x1b[<4;8;2M',
    (filter) => {
      filter.mouse.on('event', (event) => events.push(event));
    },
  );

  assert.equal(output, '');
  assert.deepEqual(events, [
    { type: 'press', button: 'left', col: 4, row: 2 },
    { type: 'move', button: 'left', col: 6, row: 2 },
    { type: 'release', button: 'left', col: 6, row: 2 },
  ]);
});

test('поглощает bracketed paste markers и сохраняет переносы как Ctrl+J', async () => {
  const { output, filter } = await runFilter('\x1b[200~первая\r\nвторая\rтретья\x1b[201~');
  assert.equal(output, 'первая\nвторая\nтретья');
  assert.equal(filter.isPasting, false);
});

test('декодирует Kitty Ctrl+V и modified Enter', async () => {
  let newlineEvents = 0;
  const { output } = await runFilter('\x1b[118;5u\x1b[13;2u', (filter) => {
    filter.keys.on('newline-key', () => { newlineEvents++; });
  });

  assert.equal(output, '\x16');
  assert.equal(newlineEvents, 1);
});

test('декодирует Kitty Cmd+C в отдельное copy-событие', async () => {
  let copyEvents = 0;
  const { output } = await runFilter('\x1b[99;9u', (filter) => {
    filter.keys.on('copy-key', () => { copyEvents++; });
  });

  assert.equal(output, '');
  assert.equal(copyEvents, 1);
});

test('различает одиночный Escape и legacy Alt+Enter', async () => {
  const escapeModifiers: number[] = [];
  let newlineEvents = 0;
  const escape = await runFilter('\x1b', (filter) => {
    filter.keys.on('escape-key', (event: { modifiers: number }) => {
      escapeModifiers.push(event.modifiers);
    });
  });
  const shiftEscape = await runFilter('\x1b[27;2u', (filter) => {
    filter.keys.on('escape-key', (event: { modifiers: number }) => {
      escapeModifiers.push(event.modifiers);
    });
  });
  const altEnter = await runFilter('\x1b\r', (filter) => {
    filter.keys.on('newline-key', () => { newlineEvents++; });
  });

  assert.equal(escape.output, '');
  assert.equal(shiftEscape.output, '');
  assert.equal(altEnter.output, '');
  assert.deepEqual(escapeModifiers, [1, 2]);
  assert.equal(newlineEvents, 1);
});
