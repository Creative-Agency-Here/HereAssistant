import assert from 'node:assert/strict';
import test from 'node:test';
import {
  deleteHaSession,
  isValidHaSessionId,
  loadHaSession,
} from './ha-sessions.js';

test('принимает только локальные идентификаторы HA-сессий', () => {
  assert.equal(isValidHaSessionId('ha-mg1234-ab12cd'), true);
  assert.equal(isValidHaSessionId('../../private'), false);
  assert.equal(isValidHaSessionId('/tmp/private'), false);
  assert.equal(isValidHaSessionId('ha-valid/../../private'), false);
});

test('load/delete отклоняют path traversal до обращения к файлу', () => {
  assert.equal(loadHaSession('../../private'), null);
  assert.equal(deleteHaSession('../../private'), false);
});
