// Структурный тип публикации: ровно те поля, которые нужны правилу привязки.
// Полный RcPublication из composables/useRemoteControl подходит под него, но
// импортировать его здесь незачем — иначе получилась бы циклическая ссылка
// (composable импортирует этот модуль как значение).
export interface RcBindablePublication {
  id: string
  deviceId: string
  conversationId: string | null
  state: string
  publishedAt: string
  expiresAt: string
}

export const RC_TERMINAL_PUBLICATION_STATES: readonly string[]

export function isRcPublicationTerminalState(state: string): boolean

export function isRcPublicationLive(
  publication: RcBindablePublication | null | undefined,
  nowMs?: number,
): boolean

export function resolveRcSessionPublication<T extends RcBindablePublication>(input: {
  conversationId?: string | null
  deviceIds?: readonly string[] | null
  publications?: readonly T[] | null
  nowMs?: number
}): T | null
