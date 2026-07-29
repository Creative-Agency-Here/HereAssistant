// Оркестрация режима /rc для WebApp HereAssistant (этап P7,
// docs/plans/active/RC_REMOTE_CONTROL_PLAN.md, раздел «Ответ 9. UI»).
//
// Браузер ходит ТОЛЬКО в свой серверный прокси (/api/rc/*), который держит
// CRM-токен на сервере. Никаких device/sync credential во фронте нет. Контракт
// полей и кодов ошибок зеркалит админку HereCRM (useGitAiRemoteControl.ts) и
// DTO бэкенда (remote-control.dto.ts): полей сверх объявленных там нет.
//
// GET /api/rc/publications отдаёт ВСЕ публикации владельца одним списком без
// conversationId — сопоставление с устройством идёт по deviceId. У браузера нет
// маршрута чтения статуса отдельной команды (он есть только у раннера), поэтому
// «очередь» ниже — локальный журнал команд, поставленных ЭТИМ браузером.

import type { ComputedRef, Ref } from 'vue'

// Типы команд, которые браузер вправе поставить (RC_DURABLE_COMMAND_TYPES бэка).
export type RcCommandType = 'prompt' | 'stop' | 'git_commit' | 'git_push'

export type RcCommandStatus =
  | 'pending'
  | 'claimed'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'indeterminate'

export type RcPublicationState =
  | 'unpublished'
  | 'published_idle'
  | 'queued_local'
  | 'running_local'
  | 'queued_remote'
  | 'running_remote'
  | 'awaiting_local_approval'
  | 'stopping'
  | 'offline'
  | 'expired'
  | 'revoked'
  | 'closed'
  | 'failed'

export interface RcCapabilities {
  remotePrompt?: boolean
  stop?: boolean
  gitCommit?: boolean
  gitPush?: boolean
  toolEvents?: boolean
}

// Публикация живой сессии (RemotePublicationResponseDto бэка).
export interface RcPublication {
  id: string
  publicId: string
  tenantId: string
  ownerUserId: number
  deviceId: string
  privacyMode: 'private' | 'crm'
  state: RcPublicationState
  capabilities: RcCapabilities | null
  runnerEpoch: number
  publishedAt: string
  lastHeartbeatAt: string
  expiresAt: string
  closedAt: string | null
  closeReason: string | null
}

// Ответ постановки команды (RemoteCommandResponseDto бэка).
export interface RcCommandResponse {
  id: string
  publicationId: string
  sequence: number
  commandType: RcCommandType
  status: RcCommandStatus
  runnerEpoch: number
  payload: Record<string, unknown> | null
  createdAt: string
  expiresAt: string
  created: boolean
}

// Терминальные состояния публикации: новые команды не принимаются (зеркалит
// PUBLICATION_TERMINAL_STATES бэка).
const RC_TERMINAL_STATES = new Set<RcPublicationState>(['closed', 'expired', 'revoked', 'failed'])

export function isRcPublicationTerminal(state: RcPublicationState): boolean {
  return RC_TERMINAL_STATES.has(state)
}

// Offline после трёх пропущенных heartbeat: heartbeat 15 секунд, offline после
// 45 секунд (раздел «Ответ 6. Безопасность» плана).
const RC_HEARTBEAT_OFFLINE_MS = 45_000
const RC_POLL_INTERVAL_MS = 12_000
const RC_TICK_INTERVAL_MS = 5_000

// Публикация принимает stop как осмысленное действие, только пока что-то реально
// выполняется или ждёт подтверждения — иначе это команда в никуда.
const RC_STOPPABLE_STATES = new Set<RcPublicationState>([
  'running_local',
  'running_remote',
  'awaiting_local_approval',
])

// Состояния бейджа устройства (раздел «Ответ 7. Индикация устройства»).
export type RcDeviceBadgeState =
  | 'online'
  | 'busy_local'
  | 'busy_remote'
  | 'awaiting_approval'
  | 'offline'
  | 'revoked'
  | 'unpublished'

export function rcDeviceBadgeState(
  pub: RcPublication | null,
  online: boolean,
): RcDeviceBadgeState {
  if (!pub) return 'unpublished'
  if (pub.state === 'revoked') return 'revoked'
  if (!online || isRcPublicationTerminal(pub.state)) return 'offline'
  if (pub.state === 'awaiting_local_approval') return 'awaiting_approval'
  if (pub.state === 'running_local' || pub.state === 'queued_local') return 'busy_local'
  if (pub.state === 'running_remote' || pub.state === 'queued_remote' || pub.state === 'stopping') {
    return 'busy_remote'
  }
  return 'online'
}

// Причина, по которой отправка prompt недоступна. null — доступна. Отсутствие
// публикации/capability трактуется как запрет: неизвестная capability не даёт
// кнопку, а не показывает её disabled.
export type RcComposerBlockReason =
  | 'no_publication'
  | 'private_presence_only'
  | 'capability_unavailable'
  | 'device_offline'
  | 'publication_closed'

// Локальный журнал команды, поставленной этим браузером.
export interface RcQueueItem {
  id: string
  publicationId: string
  commandType: RcCommandType
  status: RcCommandStatus
  sequence: number
  createdAt: string
  expiresAt: string
  created: boolean
  // Сервер не эхо-возвращает текст команды, поэтому превью — то, что реально
  // ушло с этого браузера в этом вызове (только для commandType === 'prompt').
  promptPreview: string | null
}

// Серверные коды ошибок (как в админке). Прокси отдаёт их в теле { error: code }.
export type RcServerErrorCode =
  | 'DEVICE_OFFLINE'
  | 'PUBLICATION_EXPIRED'
  | 'PRIVACY_DENIED'
  | 'CAPABILITY_UNAVAILABLE'
  | 'UNKNOWN'

export const RC_ERROR_TEXT: Record<RcServerErrorCode, string> = {
  DEVICE_OFFLINE: 'Устройство офлайн — команда не принята.',
  PUBLICATION_EXPIRED: 'Публикация истекла — сессия больше не принимает команды.',
  PRIVACY_DENIED: 'Политика приватности запрещает это действие.',
  CAPABILITY_UNAVAILABLE: 'Устройство не разрешило это действие.',
  UNKNOWN: 'Не удалось выполнить действие удалённого управления.',
}

// Достаёт серверный код ошибки из ответа прокси ({ error: code }). $fetch кладёт
// тело ответа в .data; нормализуем регистр, чтобы коды бэка (верхний регистр) и
// возможные строчные варианты совпадали.
export function rcErrorCode(error: unknown): RcServerErrorCode {
  const data = (error as { data?: { error?: unknown } })?.data
  const raw = typeof data?.error === 'string' ? data.error.trim().toUpperCase() : ''
  if (raw === 'DEVICE_OFFLINE') return 'DEVICE_OFFLINE'
  if (raw === 'PUBLICATION_EXPIRED') return 'PUBLICATION_EXPIRED'
  if (raw === 'PRIVACY_DENIED') return 'PRIVACY_DENIED'
  if (raw === 'CAPABILITY_UNAVAILABLE') return 'CAPABILITY_UNAVAILABLE'
  return 'UNKNOWN'
}

export function rcErrorText(error: unknown, fallback = RC_ERROR_TEXT.UNKNOWN): string {
  const code = rcErrorCode(error)
  return code === 'UNKNOWN' ? fallback : RC_ERROR_TEXT[code]
}

export interface RemoteControlContext {
  publications: ComputedRef<RcPublication[]>
  publication: ComputedRef<RcPublication | null>
  deviceOnline: ComputedRef<boolean>
  badgeState: ComputedRef<RcDeviceBadgeState>
  loading: Ref<boolean>
  loadError: Ref<string>
  actionError: Ref<string>
  queue: ComputedRef<RcQueueItem[]>
  sendingPrompt: Ref<boolean>
  sendingStop: Ref<boolean>
  closing: Ref<boolean>
  promptBlockReason: ComputedRef<RcComposerBlockReason | null>
  canSendPrompt: ComputedRef<boolean>
  canStop: ComputedRef<boolean>
  canClose: ComputedRef<boolean>
  refresh: () => Promise<void>
  sendPrompt: (text: string) => Promise<void>
  sendStop: () => Promise<void>
  closePublication: () => Promise<void>
}

// deviceId выбирает публикацию конкретного устройства из списка владельца. Если
// он null, publication — самая свежая публикация (список уже отсортирован бэком
// по publishedAt desc).
export function useRemoteControl(deviceId: Ref<string | null> = ref(null)): RemoteControlContext {
  const publications = ref<RcPublication[]>([])
  const allQueueItems = ref<RcQueueItem[]>([])
  const loading = ref(false)
  const loadError = ref('')
  const actionError = ref('')
  const sendingPrompt = ref(false)
  const sendingStop = ref(false)
  const closing = ref(false)
  const now = ref(Date.now())

  const publication = computed<RcPublication | null>(() => {
    if (!publications.value.length) return null
    const id = deviceId.value
    if (!id) return publications.value[0] ?? null
    return publications.value.find((item) => item.deviceId === id) ?? null
  })

  const queue = computed(() => {
    const pub = publication.value
    if (!pub) return []
    return allQueueItems.value.filter((item) => item.publicationId === pub.id)
  })

  const deviceOnline = computed(() => {
    const pub = publication.value
    if (!pub || pub.state === 'revoked') return false
    const lastHeartbeat = Date.parse(pub.lastHeartbeatAt)
    if (Number.isNaN(lastHeartbeat)) return false
    return now.value - lastHeartbeat < RC_HEARTBEAT_OFFLINE_MS
  })

  const badgeState = computed(() => rcDeviceBadgeState(publication.value, deviceOnline.value))

  // Кнопка рендерится только при полном совпадении условий плана:
  // backendCapability AND publication.state allows action AND device online
  // AND privacy capability. Privacy/runner уже свёрнуты бэком в capabilities.
  const promptBlockReason = computed<RcComposerBlockReason | null>(() => {
    const pub = publication.value
    if (!pub) return 'no_publication'
    if (isRcPublicationTerminal(pub.state)) return 'publication_closed'
    if (pub.privacyMode === 'private') return 'private_presence_only'
    if (pub.capabilities?.remotePrompt !== true) return 'capability_unavailable'
    if (!deviceOnline.value) return 'device_offline'
    return null
  })
  const canSendPrompt = computed(() => promptBlockReason.value === null)

  const canStop = computed(() => {
    const pub = publication.value
    if (!pub || isRcPublicationTerminal(pub.state)) return false
    if (pub.privacyMode === 'private') return false
    if (pub.capabilities?.stop !== true) return false
    if (!deviceOnline.value) return false
    return RC_STOPPABLE_STATES.has(pub.state)
  })

  // Закрыть свою публикацию (/rc off) — право владельца, не capability раннера:
  // доступно даже офлайн-устройству.
  const canClose = computed(() => {
    const pub = publication.value
    return !!pub && !isRcPublicationTerminal(pub.state)
  })

  async function refresh(): Promise<void> {
    loading.value = true
    try {
      const data = await apiFetch<RcPublication[]>('/api/rc/publications', {
        credentials: 'include',
      })
      publications.value = Array.isArray(data) ? data : []
      loadError.value = ''
    } catch (e) {
      loadError.value = rcErrorText(e, 'Не удалось получить статус удалённого управления')
    } finally {
      loading.value = false
    }
  }

  function pushQueueItem(pub: RcPublication, res: RcCommandResponse, preview: string | null) {
    allQueueItems.value.unshift({
      id: res.id,
      publicationId: pub.id,
      commandType: res.commandType,
      status: res.status,
      sequence: res.sequence,
      createdAt: res.createdAt,
      expiresAt: res.expiresAt,
      created: res.created,
      promptPreview: preview,
    })
  }

  async function sendPrompt(text: string): Promise<void> {
    const pub = publication.value
    const trimmed = text.trim()
    if (!pub || !trimmed || promptBlockReason.value || sendingPrompt.value) return
    sendingPrompt.value = true
    try {
      const res = await apiFetch<RcCommandResponse>(
        `/api/rc/publications/${pub.id}/commands`,
        {
          method: 'POST',
          credentials: 'include',
          body: { commandType: 'prompt', payload: { text: trimmed } },
        },
      )
      pushQueueItem(pub, res, trimmed)
      actionError.value = ''
    } catch (e) {
      actionError.value = rcErrorText(e, 'Не удалось отправить команду устройству')
    } finally {
      sendingPrompt.value = false
    }
  }

  async function sendStop(): Promise<void> {
    const pub = publication.value
    if (!pub || !canStop.value || sendingStop.value) return
    sendingStop.value = true
    try {
      const res = await apiFetch<RcCommandResponse>(
        `/api/rc/publications/${pub.id}/commands`,
        { method: 'POST', credentials: 'include', body: { commandType: 'stop' } },
      )
      pushQueueItem(pub, res, null)
      actionError.value = ''
    } catch (e) {
      actionError.value = rcErrorText(e, 'Не удалось отправить команду остановки')
    } finally {
      sendingStop.value = false
    }
  }

  async function closePublication(): Promise<void> {
    const pub = publication.value
    if (!pub || !canClose.value || closing.value) return
    closing.value = true
    try {
      const updated = await apiFetch<RcPublication>(`/api/rc/publications/${pub.id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      const idx = publications.value.findIndex((item) => item.id === updated.id)
      if (idx !== -1) publications.value.splice(idx, 1, updated)
      actionError.value = ''
    } catch (e) {
      actionError.value = rcErrorText(e, 'Не удалось снять публикацию')
    } finally {
      closing.value = false
    }
  }

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let tickTimer: ReturnType<typeof setInterval> | null = null

  onMounted(() => {
    void refresh()
    pollTimer = setInterval(() => void refresh(), RC_POLL_INTERVAL_MS)
    tickTimer = setInterval(() => {
      now.value = Date.now()
    }, RC_TICK_INTERVAL_MS)
  })
  onUnmounted(() => {
    if (pollTimer) clearInterval(pollTimer)
    if (tickTimer) clearInterval(tickTimer)
    pollTimer = null
    tickTimer = null
  })

  return {
    publications,
    publication,
    deviceOnline,
    badgeState,
    loading,
    loadError,
    actionError,
    queue,
    sendingPrompt,
    sendingStop,
    closing,
    promptBlockReason,
    canSendPrompt,
    canStop,
    canClose,
    refresh,
    sendPrompt,
    sendStop,
    closePublication,
  }
}
