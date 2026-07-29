// Общий пул публикаций /rc для всего WebApp.
//
// Экран сессии и список сессий показывают состояние ОДНИХ И ТЕХ ЖЕ устройств, а
// единственный доступный браузеру источник — GET /api/rc/publications. Поэтому
// опрос живёт здесь в одном экземпляре и делится между всеми подписчиками по
// счётчику: двадцать карточек списка дают один сетевой запрос, а не двадцать.
//
// Журнал поставленных команд тоже общий: пользователь отправляет промпт с экрана
// сессии, уходит в список и возвращается — журнал не должен обнуляться. Он всё
// равно живёт только в памяти вкладки: маршрута чтения статуса команды у
// браузера нет (он есть только у раннера под device-токеном), поэтому здесь
// честно хранится лишь то, что сервер вернул в ответ на POST.
//
// Файл намеренно НЕ импортирует ничего исполняемого из useRemoteControl.ts —
// только типы (стираются при сборке). Так зависимость остаётся односторонней:
// useRemoteControl → useRcPublications.

import type { Ref } from 'vue'
import type { RcPublication, RcQueueItem } from '~/composables/useRemoteControl'

const RC_POLL_INTERVAL_MS = 12_000
const RC_TICK_INTERVAL_MS = 5_000

// Потолок журнала команд вкладки: список показывается в узкой панели и не должен
// расти без предела за длинную сессию.
const RC_QUEUE_LIMIT = 50

// Коды и статусы, после которых опрос бессмысленно повторять: они не исправятся
// сами по себе, а бесконечный цикл 401 раз в 12 секунд — это шум в логах сервера
// на КАЖДОМ экране активности (в Telegram Mini App нет CRM-сессии браузера, и
// прокси /rc отвечает 401 всегда).
const RC_FATAL_ERROR_CODES = new Set(['unauthorized', 'rc_not_configured', 'rc_forbidden'])
const RC_FATAL_STATUSES = new Set([401, 403, 503])

// SPA (ssr: false), поэтому модульное состояние безопасно: второго пользователя
// в этом процессе не существует, состояние живёт ровно одну вкладку.
const publications = ref<RcPublication[]>([])
const queueItems = ref<RcQueueItem[]>([])
const loading = ref(false)
// Сырая ошибка, а не текст: перевод в человекочитаемую строку живёт в
// useRemoteControl вместе с остальным словарём кодов.
const loadErrorRaw = ref<unknown>(null)
const now = ref(Date.now())
const pollDisabled = ref(false)

let subscribers = 0
let pollTimer: ReturnType<typeof setInterval> | null = null
let tickTimer: ReturnType<typeof setInterval> | null = null
let inFlight: Promise<void> | null = null

function isFatalRcError(error: unknown): boolean {
  const err = error as { status?: number; statusCode?: number; data?: { error?: unknown } }
  const status = err?.status ?? err?.statusCode
  if (typeof status === 'number' && RC_FATAL_STATUSES.has(status)) return true
  const code = err?.data?.error
  return typeof code === 'string' && RC_FATAL_ERROR_CODES.has(code.trim())
}

export async function refreshRcPublications(): Promise<void> {
  if (pollDisabled.value) return
  // Совпавшие по времени вызовы подписчиков не должны множить запросы.
  if (inFlight) return inFlight
  loading.value = true
  inFlight = (async () => {
    try {
      const data = await apiFetch<RcPublication[]>('/api/rc/publications', {
        credentials: 'include',
      })
      publications.value = Array.isArray(data) ? data : []
      loadErrorRaw.value = null
    } catch (error) {
      loadErrorRaw.value = error
      if (isFatalRcError(error)) {
        // Публикаций нет и не будет: экран обязан остаться прежним, без /rc.
        publications.value = []
        pollDisabled.value = true
        stopTimers()
      }
    } finally {
      loading.value = false
      inFlight = null
    }
  })()
  return inFlight
}

export function replaceRcPublication(updated: RcPublication): void {
  const idx = publications.value.findIndex((item) => item.id === updated.id)
  if (idx !== -1) publications.value.splice(idx, 1, updated)
}

export function pushRcQueueItem(item: RcQueueItem): void {
  queueItems.value.unshift(item)
  if (queueItems.value.length > RC_QUEUE_LIMIT) queueItems.value.splice(RC_QUEUE_LIMIT)
}

function stopTimers(): void {
  if (pollTimer) clearInterval(pollTimer)
  if (tickTimer) clearInterval(tickTimer)
  pollTimer = null
  tickTimer = null
}

export interface RcPublicationsStore {
  publications: Ref<RcPublication[]>
  queueItems: Ref<RcQueueItem[]>
  loading: Ref<boolean>
  loadErrorRaw: Ref<unknown>
  now: Ref<number>
  refresh: () => Promise<void>
}

/** Подписывает компонент на общий опрос публикаций (счётчик подписчиков). */
export function useRcPublications(): RcPublicationsStore {
  onMounted(() => {
    subscribers += 1
    if (subscribers === 1 && !pollDisabled.value) {
      pollTimer = setInterval(() => void refreshRcPublications(), RC_POLL_INTERVAL_MS)
      tickTimer = setInterval(() => {
        now.value = Date.now()
      }, RC_TICK_INTERVAL_MS)
    }
    void refreshRcPublications()
  })
  onUnmounted(() => {
    subscribers = Math.max(0, subscribers - 1)
    if (subscribers === 0) stopTimers()
  })
  return { publications, queueItems, loading, loadErrorRaw, now, refresh: refreshRcPublications }
}
