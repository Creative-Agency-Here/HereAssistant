<script setup lang="ts">
import type { CrmSession, FeedEvent, FeedItem, FeedPage } from '~/types/activity'
import { channelLabel, providerLabel } from '~/types/activity'

type TimelineEntry =
  | { kind: 'message'; key: string; item: Extract<FeedItem, { kind: 'message' }> }
  | { kind: 'events'; key: string; events: FeedEvent[] }

const route = useRoute()
const conversationId = String(route.params.id)
const feedUrl = computed(() => `/api/crm/conversations/${encodeURIComponent(conversationId)}/feed?limit=100`)
const { data: sessions } = await useApi<CrmSession[]>('/api/crm/conversations')
const { data: feed, pending, error, refresh } = await useApi<FeedPage>(feedUrl)
const selectedEvents = ref<FeedEvent[]>([])
const selectedEvent = ref<FeedEvent | null>(null)
const stream = ref<HTMLElement | null>(null)
const awayFromBottom = ref(false)

const session = computed(() => sessions.value?.find((item) => item.id === conversationId) || null)
const timeline = computed<TimelineEntry[]>(() => {
  const result: TimelineEntry[] = []
  for (const item of feed.value?.items || []) {
    if (item.kind === 'message') {
      result.push({ kind: 'message', key: `m-${item.message.id}`, item })
      continue
    }
    const previous = result[result.length - 1]
    if (previous?.kind === 'events') previous.events.push(item.event)
    else result.push({ kind: 'events', key: `e-${item.event.id}`, events: [item.event] })
  }
  return result
})

function dateTime(value: string) {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function eventTitle(event: FeedEvent) {
  return event.payload?.name || ({ tool_call: 'Инструмент', status: 'Статус', file_change: 'Изменение файла' } as Record<string, string>)[event.eventType] || event.eventType || 'Действие'
}

function eventDetail(event: FeedEvent) {
  if (typeof event.payload?.detail === 'string' && event.payload.detail) return event.payload.detail
  return 'Подробности не переданы источником.'
}

function showEvents(events: FeedEvent[]) {
  selectedEvents.value = events
  selectedEvent.value = events.length === 1 ? events[0] : null
}

function closeSheet() {
  selectedEvents.value = []
  selectedEvent.value = null
}

function onScroll() {
  const el = stream.value
  if (!el) return
  awayFromBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight > 220
}

function scrollDown() {
  stream.value?.scrollTo({ top: stream.value.scrollHeight, behavior: 'smooth' })
}

onMounted(() => requestAnimationFrame(scrollDown))
watch(feed, () => nextTick(scrollDown))
</script>

<template>
  <div class="conversation-page">
    <header class="conversation-header">
      <button type="button" aria-label="Назад" @click="navigateTo('/activity')">‹</button>
      <div class="min-w-0">
        <h1>{{ session?.title || 'Сессия ассистента' }}</h1>
        <p>{{ session ? `${channelLabel(session.channel)} · ${session.projectName || session.cwd || 'Без проекта'}` : 'HereCRM' }}</p>
      </div>
      <span class="conversation-live"><i />{{ session?.accountProvider ? providerLabel(session.accountProvider) : 'AI' }}</span>
    </header>

    <main ref="stream" class="conversation-stream" @scroll.passive="onScroll">
      <div v-if="pending" class="activity-state">Загружаю ход сессии…</div>
      <div v-else-if="error" class="activity-state activity-state-error">
        <p>Не удалось загрузить сессию.</p>
        <button class="btn" type="button" @click="refresh">Повторить</button>
      </div>
      <template v-else>
        <div class="conversation-date">Ход работы</div>
        <template v-for="entry in timeline" :key="entry.key">
          <article v-if="entry.kind === 'message'" class="conversation-message" :class="`conversation-${entry.item.message.role || 'system'}`">
            <div class="message-author">
              <span>{{ entry.item.message.role === 'user' ? 'Вы' : entry.item.message.role === 'assistant' ? providerLabel(entry.item.message.provider) : 'Система' }}</span>
              <time>{{ dateTime(entry.item.message.createdAt) }}</time>
            </div>
            <div class="md" v-html="renderMarkdown(entry.item.message.content || '')" />
            <div v-if="entry.item.message.model || entry.item.message.deviceName" class="message-meta">
              {{ [entry.item.message.model, entry.item.message.deviceName].filter(Boolean).join(' · ') }}
            </div>
          </article>

          <button v-else class="tool-event-group" type="button" @click="showEvents(entry.events)">
            <span class="tool-event-icon">⌘</span>
            <span class="min-w-0">
              <strong>{{ entry.events.length === 1 ? eventTitle(entry.events[0]) : `${entry.events.length} действий ассистента` }}</strong>
              <small>{{ entry.events.length === 1 ? eventDetail(entry.events[0]) : entry.events.map(eventTitle).slice(0, 3).join(' · ') }}</small>
            </span>
            <b>›</b>
          </button>
        </template>
        <div v-if="!timeline.length" class="activity-empty">
          <h2>Лента пока пуста</h2>
          <p>Когда агент отправит сообщение или выполнит действие, оно появится здесь.</p>
        </div>
        <div v-if="feed?.hasMore" class="older-feed-hint">Показаны последние 100 элементов</div>
      </template>
    </main>

    <button v-if="awayFromBottom" class="scroll-down" type="button" aria-label="К последнему сообщению" @click="scrollDown">↓</button>

    <footer class="conversation-composer">
      <button type="button" @click="navigateTo('/activity?tab=connections')">
        <span><strong>Продолжить с ассистентом</strong><small>Выбрать Telegram или CLI</small></span>
        <b>›</b>
      </button>
    </footer>

    <ActivityBottomSheet
      :open="selectedEvents.length > 0"
      :title="selectedEvent ? eventTitle(selectedEvent) : 'Действия ассистента'"
      tall
      @close="closeSheet"
    >
      <template v-if="selectedEvent">
        <button v-if="selectedEvents.length > 1" class="sheet-back-button" type="button" @click="selectedEvent = null">‹ Все действия</button>
        <div class="event-detail-card">
          <span>{{ selectedEvent.eventType }}</span>
          <p>{{ eventDetail(selectedEvent) }}</p>
          <time>{{ dateTime(selectedEvent.createdAt) }}</time>
        </div>
      </template>
      <div v-else class="sheet-event-list">
        <button v-for="item in selectedEvents" :key="item.id" type="button" @click="selectedEvent = item">
          <span class="tool-event-icon">⌘</span>
          <span><strong>{{ eventTitle(item) }}</strong><small>{{ eventDetail(item) }}</small></span>
          <b>›</b>
        </button>
      </div>
    </ActivityBottomSheet>
  </div>
</template>
