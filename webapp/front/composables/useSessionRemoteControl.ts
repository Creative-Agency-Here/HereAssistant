// Связка «сессия HereCRM ↔ живая публикация /rc».
//
// Устройства НЕДОСТАТОЧНО: на одной машине живут сессии разных проектов, у них
// разные cwd и разная политика приватности. Совпадения по deviceId хватило бы,
// чтобы из карточки старой сессии проекта A промпт уехал в текущую публикацию
// проекта B — и отменить это нельзя. Поэтому связь идёт по conversationId
// публикации: его заполняет само устройство при публикации (см.
// chat_remote_control._resolve_crm_conversation_id).
//
// Пока публикация свой диалог не назвала (сессия ни разу не синхронизировалась
// либо опубликована старой версией устройства), сопоставление считается НЕ
// состоявшимся: элементы /rc не рендерятся вовсе. Это осознанно строгая
// сторона — молчаливая отправка не в тот проект хуже отсутствия кнопки.
//
// Само правило сопоставления живёт в чистом виде в ~/utils/rcBinding.mjs и
// покрыто тестами (webapp/front/tests/rc-binding.test.mjs); здесь только связь с
// реактивностью Vue.

import type { ComputedRef, Ref } from 'vue'
import type { CrmSession } from '~/types/activity'
import type {
  RcPublication,
  RcPublicationState,
  RemoteControlContext,
} from '~/composables/useRemoteControl'
import { isRcPublicationTerminal, useRemoteControl } from '~/composables/useRemoteControl'
import { useRcPublications } from '~/composables/useRcPublications'
import { resolveRcSessionPublication } from '~/utils/rcBinding.mjs'

export type RcDeviceKind = 'laptop' | 'server' | 'bot'

// Человекочитаемое состояние публикации: то, что реально происходит на
// устройстве прямо сейчас. Никаких выдуманных статусов — только те, что
// объявлены протоколом.
const RC_STATE_TEXT: Record<RcPublicationState, string> = {
  unpublished: 'Публикация не поднята',
  published_idle: 'Устройство свободно и готово принять промпт',
  queued_local: 'Занято локально — команда встанет в очередь',
  running_local: 'Агент работает локально — команда встанет в очередь',
  queued_remote: 'Команда принята и ждёт очереди на устройстве',
  running_remote: 'Агент выполняет удалённую команду',
  awaiting_local_approval: 'Агент ждёт подтверждения на самом устройстве',
  stopping: 'Останавливаю текущий запуск',
  offline: 'Устройство не на связи',
  expired: 'Публикация истекла',
  revoked: 'Доступ устройства отозван',
  closed: 'Публикация снята (/rc off)',
  failed: 'Публикация завершилась сбоем',
}

/** Тип устройства по платформе — только для иконки бейджа. */
function deviceKindOf(platform: string | null | undefined): RcDeviceKind {
  const value = (platform || '').toLowerCase()
  if (value.includes('server') || value.includes('ubuntu') || value.includes('debian')) {
    return 'server'
  }
  if (value.includes('bot')) return 'bot'
  return 'laptop'
}

export interface SessionRemoteControl {
  rc: RemoteControlContext
  /** У сессии есть хотя бы один идентификатор устройства. */
  hasDevice: ComputedRef<boolean>
  /** У ЭТОЙ сессии есть живая публикация: только тогда рисуем элементы /rc. */
  isLive: ComputedRef<boolean>
  deviceName: ComputedRef<string>
  deviceKind: ComputedRef<RcDeviceKind>
  statusText: ComputedRef<string>
}

export function useSessionRemoteControl(
  session: Ref<CrmSession | null> | ComputedRef<CrmSession | null>,
): SessionRemoteControl {
  // Диалог мог вестись с нескольких устройств: deviceId — последнее, deviceIds —
  // все. Список только СУЖАЕТ выбор публикации (см. rcBinding.mjs); у сессий
  // HereAssistant он обычно пуст, потому что синк не проставляет устройство.
  const deviceCandidates = computed<string[]>(() => {
    const item = session.value
    if (!item) return []
    const ids = new Set<string>()
    if (item.deviceId) ids.add(item.deviceId)
    for (const id of item.deviceIds || []) {
      if (id) ids.add(id)
    }
    return [...ids]
  })

  const hasDevice = computed(() => deviceCandidates.value.length > 0)

  const store = useRcPublications()

  // Живая публикация ИМЕННО этой сессии. Правило целиком в rcBinding.mjs:
  // обязательное совпадение conversationId, живое состояние публикации, а
  // устройство — только дополнительное сужение. Фолбэка «возьмём что-нибудь живое
  // с этой машины» здесь нет и быть не должно.
  const boundPublication = computed<RcPublication | null>(() =>
    resolveRcSessionPublication({
      conversationId: session.value?.id ?? null,
      deviceIds: deviceCandidates.value,
      publications: store.publications.value,
      // now общего стора тикает раз в 5 секунд, поэтому истечение TTL публикации
      // само убирает элементы управления, без собственного таймера.
      nowMs: store.now.value,
    }),
  )

  const boundPublicationId = computed<string | null>(() => boundPublication.value?.id ?? null)

  // useRemoteControl принимает ТОЛЬКО идентификатор публикации: другого способа
  // выбрать цель у него нет, поэтому «промпт улетел в чужой проект» невозможен
  // даже при ошибке вызывающего.
  const rc = useRemoteControl(boundPublicationId)

  const isLive = computed(() => boundPublication.value !== null)
  const deviceName = computed(() => session.value?.deviceName || '')
  const deviceKind = computed(() => deviceKindOf(session.value?.devicePlatform))
  const statusText = computed(() => {
    const pub = rc.publication.value
    if (!isLive.value || !pub) return ''
    if (!rc.deviceOnline.value && !isRcPublicationTerminal(pub.state)) {
      return 'Устройство пропустило heartbeat — команды сейчас не дойдут'
    }
    return RC_STATE_TEXT[pub.state] || ''
  })

  return { rc, hasDevice, isLive, deviceName, deviceKind, statusText }
}
