// Привязка «сессия HereCRM ↔ живая публикация /rc». Чистые функции без Vue,
// поэтому проверяются как обычный код (webapp/front/tests/rc-binding.test.mjs), а
// не «на глаз» в интерфейсе.
//
// ЕДИНСТВЕННЫЙ законный признак принадлежности публикации сессии — совпадение
// conversationId: колонку cli_agent_remote_publications.conversation_id заполняет
// само устройство при публикации. Устройства НЕДОСТАТОЧНО: на одной машине живут
// сессии разных проектов с разным рабочим каталогом и разной политикой
// приватности, и совпадения по deviceId хватило бы, чтобы из карточки старой
// сессии проекта A промпт уехал в текущую публикацию проекта B. Отменить такой
// запуск нечем — поэтому здесь default deny: нет точного совпадения диалога, нет
// цели, а значит и элементов управления.

// Состояния публикации, после которых команды не принимаются (зеркалит
// PUBLICATION_TERMINAL_STATES бэкенда, remote-control.shared.ts, и
// TERMINAL_PUBLICATION_STATES моста, core/remote_bridge.py). Списки обязаны
// совпадать дословно.
export const RC_TERMINAL_PUBLICATION_STATES = Object.freeze([
  'closed',
  'expired',
  'revoked',
  'failed',
])

export function isRcPublicationTerminalState(state) {
  return RC_TERMINAL_PUBLICATION_STATES.includes(state)
}

function timestampMs(value) {
  const parsed = Date.parse(typeof value === 'string' ? value : '')
  return Number.isNaN(parsed) ? null : parsed
}

/**
 * Публикация жива: состояние не терминальное И срок ещё не вышел.
 *
 * Свежесть heartbeat здесь НЕ учитывается намеренно: пропущенный heartbeat —
 * временное состояние, о котором интерфейс обязан сообщить текстом («команды
 * сейчас не дойдут»), а не убирать экран из-под человека. А вот закрытая или
 * просроченная публикация мертва окончательно — её органы управления лгали бы.
 */
export function isRcPublicationLive(publication, nowMs = Date.now()) {
  if (!publication || typeof publication !== 'object') return false
  if (isRcPublicationTerminalState(publication.state)) return false
  const expiresAt = timestampMs(publication.expiresAt)
  // Неразборный expiresAt — не повод считать публикацию живой: срок неизвестен.
  if (expiresAt === null) return false
  return expiresAt > nowMs
}

/**
 * Живая публикация ИМЕННО этой сессии — или null.
 *
 * @param {{
 *   conversationId?: unknown,
 *   deviceIds?: unknown,
 *   publications?: unknown,
 *   nowMs?: number,
 * }} input
 */
export function resolveRcSessionPublication(input) {
  const conversationId =
    typeof input?.conversationId === 'string' ? input.conversationId.trim() : ''
  // Сессия без идентификатора диалога не сопоставляется ни с чем. Возврат «самой
  // свежей публикации машины» здесь был бы отправкой в чужой проект.
  if (!conversationId) return null

  const publications = Array.isArray(input?.publications) ? input.publications : []
  const deviceIds = Array.isArray(input?.deviceIds)
    ? input.deviceIds.filter((id) => typeof id === 'string' && id)
    : []
  const nowMs = typeof input?.nowMs === 'number' ? input.nowMs : Date.now()

  let best = null
  let bestPublishedAt = -1
  for (const publication of publications) {
    if (!publication || typeof publication !== 'object') continue
    // Сверка диалога — обязательное условие, а не одно из нескольких «или».
    if (publication.conversationId !== conversationId) continue
    if (!isRcPublicationLive(publication, nowMs)) continue
    // Устройство сессии, если оно вообще известно, может только СУЗИТЬ выбор:
    // расширить его совпадением по машине запрещено (см. шапку файла). У сессий
    // HereAssistant device_id не заполняется, поэтому список часто пуст — тогда
    // достаточно совпадения диалога.
    if (deviceIds.length && !deviceIds.includes(publication.deviceId)) continue
    // Один диалог = один проект, поэтому при нескольких живых публикациях берём
    // самую свежую: это та же сессия, просто переопубликованная.
    const publishedAt = timestampMs(publication.publishedAt) ?? 0
    if (publishedAt > bestPublishedAt) {
      best = publication
      bestPublishedAt = publishedAt
    }
  }
  return best
}
