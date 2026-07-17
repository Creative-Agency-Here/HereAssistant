import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  diffLines,
  eventKind,
  eventSummary,
  eventTitle,
  formatDuration,
  lineRange,
  resolveSheetGesture,
  statusInfo,
} from '../utils/activityEvents.mjs'

const fixtures = JSON.parse(await readFile(new URL('./fixtures/activity-events.json', import.meta.url), 'utf8'))

test('витрина содержит все пять режимов', () => {
  assert.deepEqual(fixtures.map(eventKind), ['read', 'edit', 'write', 'bash', 'agent'])
  for (const event of fixtures) {
    assert.notEqual(eventTitle(event), 'Действие ассистента')
    assert.notEqual(eventSummary(event), 'Подробности не переданы источником.')
    assert.deepEqual(statusInfo(event), { label: 'Готово', className: 'is-success' })
  }
})

test('Read показывает диапазон строк, Bash — длительность', () => {
  assert.equal(lineRange(fixtures[0].payload), 'строки 72–89')
  assert.equal(formatDuration(fixtures[3].payload.durationMs), '4.3 с')
})

test('Edit формирует безопасный визуальный diff', () => {
  const lines = diffLines(fixtures[1].payload.before, fixtures[1].payload.after)
  assert.ok(lines.some((line) => line.type === 'remove'))
  assert.ok(lines.some((line) => line.type === 'add'))
})

test('в публичных примерах нет секретных токенов', () => {
  const raw = JSON.stringify(fixtures)
  assert.doesNotMatch(raw, /hvs\.[\w-]+|sk-[A-Za-z0-9_-]{16,}|\d{8,12}:[A-Za-z0-9_-]{20,}/)
})

test('жесты раскрывают, сворачивают и закрывают bottom sheet', () => {
  assert.equal(resolveSheetGesture(-80, false), 'expand')
  assert.equal(resolveSheetGesture(90, true), 'collapse')
  assert.equal(resolveSheetGesture(180, false), 'close')
  assert.equal(resolveSheetGesture(20, true), 'keep')
})
