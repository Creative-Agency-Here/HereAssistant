// Правило привязки «сессия ↔ публикация /rc» (webapp/front/utils/rcBinding.mjs).
//
// Тест защищает главное свойство экрана сессии: элементы управления целятся в ЭТУ
// сессию, а не в устройство. Если сопоставление снова начнёт срабатывать по
// deviceId без сверки диалога, промпт из карточки старой сессии проекта A уедет в
// текущую публикацию проекта B того же компьютера — с чужим рабочим каталогом и
// чужой политикой приватности. Отменить такой запуск нечем.

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  RC_TERMINAL_PUBLICATION_STATES,
  isRcPublicationLive,
  resolveRcSessionPublication,
} from '../utils/rcBinding.mjs'

const NOW = Date.parse('2026-07-30T12:00:00.000Z')
const SOON = new Date(NOW + 20 * 60_000).toISOString()
const PAST = new Date(NOW - 60_000).toISOString()

const DEVICE = '11111111-1111-4111-8111-111111111111'
const OTHER_DEVICE = '22222222-2222-4222-8222-222222222222'
const CONVERSATION_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const CONVERSATION_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

function publication(overrides = {}) {
  return {
    id: 'pub-default',
    deviceId: DEVICE,
    conversationId: CONVERSATION_A,
    state: 'published_idle',
    publishedAt: new Date(NOW - 60_000).toISOString(),
    expiresAt: SOON,
    ...overrides,
  }
}

test('цель ищется по диалогу сессии, а не по машине', () => {
  // Обе публикации живут на ОДНОМ компьютере, но это разные проекты.
  const foreign = publication({ id: 'pub-b', conversationId: CONVERSATION_B })
  const own = publication({ id: 'pub-a', conversationId: CONVERSATION_A })
  const match = resolveRcSessionPublication({
    conversationId: CONVERSATION_A,
    deviceIds: [DEVICE],
    // Чужая публикация первая в списке: сопоставление «по устройству» вернуло бы её.
    publications: [foreign, own],
    nowMs: NOW,
  })
  assert.equal(match?.id, 'pub-a')
})

test('живая публикация того же устройства без совпадения диалога цели не даёт', () => {
  const foreign = publication({ id: 'pub-b', conversationId: CONVERSATION_B })
  const match = resolveRcSessionPublication({
    conversationId: CONVERSATION_A,
    deviceIds: [DEVICE],
    publications: [foreign],
    nowMs: NOW,
  })
  assert.equal(match, null)
})

test('публикация без диалога не привязывается ни к одной сессии', () => {
  const orphan = publication({ id: 'pub-orphan', conversationId: null })
  assert.equal(
    resolveRcSessionPublication({
      conversationId: CONVERSATION_A,
      deviceIds: [DEVICE],
      publications: [orphan],
      nowMs: NOW,
    }),
    null,
  )
  // И наоборот: сессия без id не имеет права ни на какую публикацию.
  assert.equal(
    resolveRcSessionPublication({
      conversationId: null,
      deviceIds: [DEVICE],
      publications: [publication()],
      nowMs: NOW,
    }),
    null,
  )
})

test('мёртвая публикация целью не становится', () => {
  for (const state of RC_TERMINAL_PUBLICATION_STATES) {
    const dead = publication({ id: `pub-${state}`, state })
    assert.equal(
      resolveRcSessionPublication({
        conversationId: CONVERSATION_A,
        publications: [dead],
        nowMs: NOW,
      }),
      null,
      `состояние ${state} обязано считаться мёртвым`,
    )
  }
  const expired = publication({ id: 'pub-expired', expiresAt: PAST })
  assert.equal(
    resolveRcSessionPublication({
      conversationId: CONVERSATION_A,
      publications: [expired],
      nowMs: NOW,
    }),
    null,
  )
})

test('устройство сессии может только сузить выбор, но не расширить', () => {
  // Диалог совпал, но опубликовала его другая машина — цели нет.
  const moved = publication({ id: 'pub-moved', deviceId: OTHER_DEVICE })
  assert.equal(
    resolveRcSessionPublication({
      conversationId: CONVERSATION_A,
      deviceIds: [DEVICE],
      publications: [moved],
      nowMs: NOW,
    }),
    null,
  )
  // У сессий HereAssistant устройство не заполняется: пустой список сужений не
  // добавляет, и привязка обязана состояться по одному диалогу.
  assert.equal(
    resolveRcSessionPublication({
      conversationId: CONVERSATION_A,
      deviceIds: [],
      publications: [moved],
      nowMs: NOW,
    })?.id,
    'pub-moved',
  )
})

test('при нескольких живых публикациях одного диалога берётся самая свежая', () => {
  const older = publication({
    id: 'pub-older',
    publishedAt: new Date(NOW - 10 * 60_000).toISOString(),
  })
  const newer = publication({
    id: 'pub-newer',
    publishedAt: new Date(NOW - 60_000).toISOString(),
  })
  const match = resolveRcSessionPublication({
    conversationId: CONVERSATION_A,
    publications: [older, newer],
    nowMs: NOW,
  })
  assert.equal(match?.id, 'pub-newer')
})

test('isRcPublicationLive держит терминальные состояния и срок', () => {
  assert.equal(isRcPublicationLive(publication(), NOW), true)
  assert.equal(isRcPublicationLive(publication({ state: 'closed' }), NOW), false)
  assert.equal(isRcPublicationLive(publication({ expiresAt: PAST }), NOW), false)
  assert.equal(isRcPublicationLive(publication({ expiresAt: 'не дата' }), NOW), false)
  assert.equal(isRcPublicationLive(null, NOW), false)
})

test('список терминальных состояний совпадает с контрактом бэкенда', () => {
  assert.deepEqual([...RC_TERMINAL_PUBLICATION_STATES].sort(), [
    'closed',
    'expired',
    'failed',
    'revoked',
  ])
})
