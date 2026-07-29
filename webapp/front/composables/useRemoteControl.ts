// Оркестрация режима /rc для WebApp HereAssistant (этап P7,
// docs/plans/active/RC_REMOTE_CONTROL_PLAN.md, раздел «Ответ 9. UI»).
//
// Браузер ходит ТОЛЬКО в свой серверный прокси (/api/rc/*), который держит
// CRM-токен на сервере. Никаких device/sync credential во фронте нет. Контракт
// полей и кодов ошибок зеркалит админку HereCRM (useGitAiRemoteControl.ts) и
// DTO бэкенда (remote-control.dto.ts): полей сверх объявленных там нет.
//
// GET /api/rc/publications отдаёт ВСЕ публикации владельца одним списком. Строка
// публикации приходит целиком (сервер делает select без проекции), поэтому в ней
// есть conversationId — точная связь с КОНКРЕТНОЙ сессией; deviceId связывает
// лишь с машиной, на которой сессий может быть много. Поэтому цель этому
// composable передают уже готовым идентификатором публикации (правило поиска —
// ~/utils/rcBinding.mjs), а сам он выбирать публикацию не умеет.
//
// У браузера нет маршрута чтения статуса отдельной команды (он есть только у
// раннера), поэтому «очередь» ниже — локальный журнал команд, поставленных ЭТИМ
// браузером.

import type { ComputedRef, Ref } from 'vue'
import {
  pushRcQueueItem,
  replaceRcPublication,
  useRcPublications,
} from '~/composables/useRcPublications'
import { RC_TERMINAL_PUBLICATION_STATES } from '~/utils/rcBinding.mjs'

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

// Потолок промпта — тот же, что у бэкенда (RcPromptPayloadDto, MaxLength(8000)) и
// у прокси (MAX_PROMPT_CHARS). Держим его на клиенте, чтобы человек узнал о
// превышении до отправки, а не из общего 400.
export const RC_PROMPT_MAX_CHARS = 8000

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
  // Диалог CRM, к которому привязана публикация. null, пока сессия ни разу не
  // синхронизировалась — тогда доказать принадлежность конкретной сессии нечем.
  conversationId: string | null
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

// Команда в ответе постановки. Плоский Swagger-DTO (RemoteCommandResponseDto)
// недостоверен: сервис возвращает createCommandTx как есть, то есть строку
// команды и флаг created ОТДЕЛЬНЫМИ полями конверта (см. RcCommandEnvelope).
export interface RcCommand {
  id: string
  publicationId: string
  sequence: number
  commandType: RcCommandType
  status: RcCommandStatus
  runnerEpoch: number
  payload: Record<string, unknown> | null
  createdAt: string
  expiresAt: string
}

// Фактический ответ POST .../commands: { command, created }.
export interface RcCommandEnvelope {
  command: RcCommand
  created: boolean
}

// Терминальные состояния публикации: новые команды не принимаются. Список один
// на весь WebApp — в ~/utils/rcBinding.mjs (зеркалит PUBLICATION_TERMINAL_STATES
// бэка). Второй копии здесь быть не должно: расхождение списков означает кнопку
// на мёртвой публикации.
const RC_TERMINAL_STATES = new Set<string>(RC_TERMINAL_PUBLICATION_STATES)

export function isRcPublicationTerminal(state: RcPublicationState): boolean {
  return RC_TERMINAL_STATES.has(state)
}

// Offline после трёх пропущенных heartbeat: heartbeat 15 секунд, offline после
// 45 секунд (раздел «Ответ 6. Безопасность» плана). Сам опрос публикаций и тик
// времени живут в общем сторе useRcPublications — один на вкладку.
const RC_HEARTBEAT_OFFLINE_MS = 45_000

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

// Коды отказа сервера, которые прокси отдаёт браузеру в теле { error: code }.
// Закрытый список — тот же, что ставит бэкенд на постановке команды: только эти
// строки означают «сервер отказал по известной причине». Придуманных кодов
// (PUBLICATION_EXPIRED и т. п.) в контракте нет — их нельзя показывать как
// причину, иначе интерфейс объясняет отказ, которого не было.
export type RcServerErrorCode =
  | 'DEVICE_OFFLINE'
  | 'PUBLICATION_CLOSED'
  | 'CAPABILITY_UNAVAILABLE'
  | 'IDEMPOTENCY_KEY_REQUIRED'
  | 'IDEMPOTENCY_KEY_INVALID'
  | 'UNKNOWN'

export const RC_ERROR_TEXT: Record<RcServerErrorCode, string> = {
  DEVICE_OFFLINE: 'Устройство офлайн — команда не принята.',
  PUBLICATION_CLOSED: 'Публикация закрыта или просрочена — команды больше не принимаются.',
  CAPABILITY_UNAVAILABLE: 'Устройство не разрешило это действие.',
  IDEMPOTENCY_KEY_REQUIRED: 'Команда отправлена без ключа идемпотентности — повторите отправку.',
  IDEMPOTENCY_KEY_INVALID: 'Ключ идемпотентности не принят сервером — повторите отправку.',
  UNKNOWN: 'Не удалось выполнить действие удалённого управления.',
}

const RC_SERVER_ERROR_CODES = new Set<string>([
  'DEVICE_OFFLINE',
  'PUBLICATION_CLOSED',
  'CAPABILITY_UNAVAILABLE',
  'IDEMPOTENCY_KEY_REQUIRED',
  'IDEMPOTENCY_KEY_INVALID',
])

// Коды самого прокси WebApp (webapp/api/routes/remote_control.py). Это отказ
// НАШЕГО сервера до исходящего запроса к HereCRM, а не ответ бэкенда, поэтому
// список держится отдельно от закрытого набора серверных кодов. Без него человек
// на любую такую причину видел бы одну и ту же общую фразу.
const RC_PROXY_ERROR_TEXT: Record<string, string> = {
  prompt_too_long: `Промпт длиннее ${RC_PROMPT_MAX_CHARS} символов — сократите текст.`,
  body_too_large: 'Сообщение слишком велико для отправки.',
  invalid_command: 'Команда не принята: неверное тело запроса.',
  invalid_json: 'Команда не принята: тело запроса не разобрано.',
  invalid_idempotency_key: 'Ключ идемпотентности не прошёл проверку — повторите отправку.',
  invalid_publication_id: 'Публикация адресована неверно — обновите страницу.',
  rc_not_configured: 'Удалённое управление не настроено на сервере.',
  unauthorized: 'Нужен вход в HereCRM: удалённое управление доступно только владельцу.',
  not_owner: 'Удалённое управление доступно только владельцу устройства.',
  rc_forbidden: 'Доступ к удалённому управлению запрещён.',
  rc_not_found: 'Публикация не найдена — сессия могла быть снята.',
  rc_conflict: 'Публикация закрыта или устройство офлайн — команда не принята.',
  crm_unavailable: 'HereCRM недоступна — команда не отправлена.',
}

/** Сырая строка кода из тела ответа прокси ({ error: code }); '' — кода нет. */
function rawRcErrorCode(error: unknown): string {
  const data = (error as { data?: { error?: unknown } })?.data
  return typeof data?.error === 'string' ? data.error.trim() : ''
}

// Серверный код отказа. $fetch кладёт тело ответа в .data; регистр нормализуем,
// чтобы коды бэка (верхний регистр) совпадали и в строчном варианте.
export function rcErrorCode(error: unknown): RcServerErrorCode {
  const raw = rawRcErrorCode(error).toUpperCase()
  return RC_SERVER_ERROR_CODES.has(raw) ? (raw as RcServerErrorCode) : 'UNKNOWN'
}

export function rcErrorText(error: unknown, fallback = RC_ERROR_TEXT.UNKNOWN): string {
  const code = rcErrorCode(error)
  if (code !== 'UNKNOWN') return RC_ERROR_TEXT[code]
  return RC_PROXY_ERROR_TEXT[rawRcErrorCode(error).toLowerCase()] || fallback
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
  // true — команда принята сервером. Композер по этому признаку решает, можно ли
  // очистить поле: при отказе текст обязан остаться, иначе человек потеряет
  // написанное и наберёт заново — то есть с новым ключом идемпотентности.
  sendPrompt: (text: string) => Promise<boolean>
  sendStop: () => Promise<void>
  closePublication: () => Promise<void>
}

// Ключ идемпотентности постановки промпта. Прокси пробрасывает его в заголовке
// Idempotency-Key, бэкенд снимает дубль по паре (publicationId, ключ). Без него
// сервер генерирует случайный ключ сам, и повтор после сетевого сбоя создаёт
// ВТОРОЙ промпт — то есть второй запуск агента на устройстве.
function newIdempotencyKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return `web_${uuid}`
  return `web_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`
}

// Срок жизни ключа неудавшейся попытки. Дальше он обязан протухнуть: иначе
// намеренная повторная отправка ТОГО ЖЕ текста через час вернула бы старую
// команду (created:false) вместо новой, и промпт молча не выполнился бы.
const RC_IDEMPOTENCY_TTL_MS = 5 * 60_000

// Цель задаётся ТОЛЬКО идентификатором публикации, и он обязателен. Выбор
// «по устройству» или «самая свежая публикация владельца» здесь недопустим: у
// одной машины публикаций много, они принадлежат разным проектам с разным
// рабочим каталогом и разной политикой приватности, а отменить уже отправленный
// промпт нечем. Кто эту публикацию нашёл — решает ~/utils/rcBinding.mjs.
export function useRemoteControl(
  publicationId: Ref<string | null> | ComputedRef<string | null>,
): RemoteControlContext {
  const store = useRcPublications()
  const publications = computed<RcPublication[]>(() => store.publications.value)
  const loading = store.loading
  const loadError = computed(() =>
    store.loadErrorRaw.value
      ? rcErrorText(store.loadErrorRaw.value, 'Не удалось получить статус удалённого управления')
      : '',
  )
  const actionError = ref('')
  const sendingPrompt = ref(false)
  const sendingStop = ref(false)
  const closing = ref(false)
  const now = store.now
  // Ключ последней НЕ подтверждённой сервером попытки. Повторная отправка того же
  // текста переиспользует его, поэтому «отправил → сеть моргнула → отправил ещё
  // раз» даёт один промпт на устройстве, а не два.
  const pendingAttempt = ref<{ key: string; text: string; at: number } | null>(null)

  const publication = computed<RcPublication | null>(() => {
    // Пустой publicationId означает «цели нет», а НЕ «возьми любую публикацию
    // машины»: без цели интерфейс обязан остаться читающим.
    const exact = publicationId.value
    if (!exact) return null
    return publications.value.find((item) => item.id === exact) ?? null
  })

  const queue = computed(() => {
    const pub = publication.value
    if (!pub) return []
    return store.queueItems.value.filter((item) => item.publicationId === pub.id)
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

  const refresh = store.refresh

  function pushQueueItem(pub: RcPublication, res: RcCommandEnvelope, preview: string | null) {
    const command = res?.command
    // Форма ответа не совпала с контрактом — журнал молча не заполняем строкой
    // из undefined: пустой журнал честнее выдуманной записи.
    if (!command?.id) return
    pushRcQueueItem({
      id: command.id,
      publicationId: pub.id,
      commandType: command.commandType,
      status: command.status,
      sequence: command.sequence,
      createdAt: command.createdAt,
      expiresAt: command.expiresAt,
      created: res.created !== false,
      promptPreview: preview,
    })
  }

  async function sendPrompt(text: string): Promise<boolean> {
    const pub = publication.value
    const trimmed = text.trim()
    if (!pub || !trimmed || promptBlockReason.value || sendingPrompt.value) return false
    if (trimmed.length > RC_PROMPT_MAX_CHARS) {
      // Бэкенд отклонит такой промпт валидатором; лучше сказать это сразу и
      // сохранить текст, чем отправить в никуда.
      actionError.value = RC_PROXY_ERROR_TEXT.prompt_too_long
      return false
    }
    // Тот же текст сразу после неудачной попытки уходит под тем же ключом:
    // сервер вернёт уже созданную команду (created:false), а не заведёт вторую.
    // По истечении TTL ключ протухает — это уже осознанная новая отправка.
    const previous = pendingAttempt.value
    const attempt =
      previous && previous.text === trimmed && Date.now() - previous.at < RC_IDEMPOTENCY_TTL_MS
        ? previous
        : { key: newIdempotencyKey(), text: trimmed, at: Date.now() }
    pendingAttempt.value = attempt
    sendingPrompt.value = true
    try {
      const res = await apiFetch<RcCommandEnvelope>(
        `/api/rc/publications/${pub.id}/commands`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Idempotency-Key': attempt.key },
          // Ключ payload.prompt — контракт раннера (chat_remote_control.py,
          // _ingest_prompt_command читает payload['prompt']). Любое другое имя
          // доедет до устройства пустой строкой.
          body: { commandType: 'prompt', payload: { prompt: trimmed } },
        },
      )
      pushQueueItem(pub, res, trimmed)
      pendingAttempt.value = null
      actionError.value = ''
      return true
    } catch (e) {
      actionError.value = rcErrorText(e, 'Не удалось отправить команду устройству')
      return false
    } finally {
      sendingPrompt.value = false
    }
  }

  async function sendStop(): Promise<void> {
    const pub = publication.value
    if (!pub || !canStop.value || sendingStop.value) return
    sendingStop.value = true
    try {
      const res = await apiFetch<RcCommandEnvelope>(
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
      replaceRcPublication(updated)
      actionError.value = ''
    } catch (e) {
      actionError.value = rcErrorText(e, 'Не удалось снять публикацию')
    } finally {
      closing.value = false
    }
  }

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
