const KIND_BY_NAME = {
  read: 'read',
  read_file: 'read',
  edit: 'edit',
  multiedit: 'edit',
  apply_patch: 'edit',
  write: 'write',
  bash: 'bash',
  exec_command: 'bash',
  shell_command: 'bash',
  agent: 'agent',
  task: 'agent',
  spawn_agent: 'agent',
}

const LABELS = {
  read: 'Прочитан файл',
  edit: 'Изменён файл',
  write: 'Создан файл',
  bash: 'Выполнена команда',
  agent: 'Запущен агент',
  other: 'Действие ассистента',
}

const ICONS = { read: 'R', edit: '±', write: 'W', bash: '›_', agent: 'A', other: '⌘' }

export function eventPayload(event) {
  return event?.payload && typeof event.payload === 'object' ? event.payload : {}
}

export function eventKind(event) {
  const payload = eventPayload(event)
  if (['read', 'edit', 'write', 'bash', 'agent'].includes(payload.kind)) return payload.kind
  const name = String(payload.name || '').toLowerCase()
  return KIND_BY_NAME[name] || 'other'
}

export function eventTitle(event) {
  const kind = eventKind(event)
  if (kind !== 'other') return LABELS[kind]
  return eventPayload(event).name || LABELS.other
}

export function eventIcon(event) {
  return ICONS[eventKind(event)]
}

export function eventSummary(event) {
  const payload = eventPayload(event)
  const kind = eventKind(event)
  if (['read', 'edit', 'write'].includes(kind) && payload.path) return String(payload.path)
  if (kind === 'bash' && payload.command) return String(payload.command).split('\n')[0]
  if (kind === 'agent' && payload.task) return String(payload.task).split('\n')[0]
  if (typeof payload.detail === 'string' && payload.detail) return payload.detail
  return 'Подробности не переданы источником.'
}

export function statusInfo(event) {
  const status = eventPayload(event).status
  if (status === 'error') return { label: 'Ошибка', className: 'is-error' }
  if (status === 'running') return { label: 'Выполняется', className: 'is-running' }
  return { label: 'Готово', className: 'is-success' }
}

export function formatDuration(value) {
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)} мс`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} с`
}

export function lineRange(payload) {
  if (!Number.isInteger(payload?.lineStart)) return ''
  if (!Number.isInteger(payload?.lineCount) || payload.lineCount < 1) return `строка ${payload.lineStart}`
  return `строки ${payload.lineStart}–${payload.lineStart + payload.lineCount - 1}`
}

export function diffLines(before, after) {
  const oldLines = String(before || '').split('\n')
  const newLines = String(after || '').split('\n')
  let prefix = 0
  while (prefix < oldLines.length && prefix < newLines.length && oldLines[prefix] === newLines[prefix]) prefix++
  let suffix = 0
  while (
    suffix < oldLines.length - prefix &&
    suffix < newLines.length - prefix &&
    oldLines[oldLines.length - 1 - suffix] === newLines[newLines.length - 1 - suffix]
  ) suffix++
  const result = []
  for (const line of oldLines.slice(Math.max(0, prefix - 2), prefix)) result.push({ type: 'same', text: line })
  for (const line of oldLines.slice(prefix, oldLines.length - suffix)) result.push({ type: 'remove', text: line })
  for (const line of newLines.slice(prefix, newLines.length - suffix)) result.push({ type: 'add', text: line })
  for (const line of newLines.slice(newLines.length - suffix, Math.min(newLines.length, newLines.length - suffix + 2))) result.push({ type: 'same', text: line })
  return result.length ? result : [{ type: 'same', text: 'Изменений в тексте нет' }]
}

export function resolveSheetGesture(delta, expanded) {
  if (delta < -48) return 'expand'
  if (delta > 150 && !expanded) return 'close'
  if (delta > 72) return 'collapse'
  return 'keep'
}
