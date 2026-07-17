<script setup lang="ts">
import type { FeedEvent } from '~/types/activity'
import {
  diffLines,
  eventIcon,
  eventKind,
  eventPayload,
  eventSummary,
  eventTitle,
  formatDuration,
  lineRange,
  statusInfo,
} from '~/utils/activityEvents.mjs'

const props = defineProps<{
  event: FeedEvent
  formattedTime: string
}>()

const payload = computed(() => eventPayload(props.event))
const kind = computed(() => eventKind(props.event))
const status = computed(() => statusInfo(props.event))
const lines = computed(() => diffLines(payload.value.before, payload.value.after))
const copied = ref('')

async function copyText(value: unknown, field: string) {
  if (typeof value !== 'string' || !value) return
  await navigator.clipboard.writeText(value)
  copied.value = field
  window.setTimeout(() => {
    if (copied.value === field) copied.value = ''
  }, 1400)
}
</script>

<template>
  <article class="event-detail">
    <header class="event-detail-hero">
      <span class="event-detail-icon">{{ eventIcon(event) }}</span>
      <span class="min-w-0 flex-1">
        <strong>{{ eventTitle(event) }}</strong>
        <small>{{ eventSummary(event) }}</small>
      </span>
      <span class="event-status" :class="status.className"><i />{{ status.label }}</span>
    </header>

    <dl v-if="payload.path || payload.cwd || payload.agentName || lineRange(payload)" class="event-facts">
      <div v-if="payload.path"><dt>Путь</dt><dd>{{ payload.path }}</dd></div>
      <div v-if="lineRange(payload)"><dt>Фрагмент</dt><dd>{{ lineRange(payload) }}</dd></div>
      <div v-if="payload.cwd"><dt>Папка</dt><dd>{{ payload.cwd }}</dd></div>
      <div v-if="payload.agentName"><dt>Агент</dt><dd>{{ payload.agentName }}</dd></div>
    </dl>

    <section v-if="payload.task" class="event-section">
      <h3>Задача агенту</h3>
      <p class="event-task">{{ payload.task }}</p>
    </section>

    <section v-if="payload.command" class="event-section">
      <header><h3>Вызвано</h3><button type="button" @click="copyText(payload.command, 'command')">{{ copied === 'command' ? 'Скопировано' : 'Копировать' }}</button></header>
      <pre class="event-code"><code>$ {{ payload.command }}</code></pre>
    </section>

    <section v-if="kind === 'edit' && (payload.before != null || payload.after != null)" class="event-section">
      <h3>Что изменилось</h3>
      <pre class="event-diff"><code><span v-for="(line, index) in lines" :key="index" :class="`diff-${line.type}`"><b>{{ line.type === 'add' ? '+' : line.type === 'remove' ? '−' : ' ' }}</b>{{ line.text || ' ' }}</span></code></pre>
    </section>

    <section v-if="payload.content && kind !== 'edit'" class="event-section">
      <header><h3>{{ kind === 'read' ? 'Прочитано' : 'Записано' }}</h3><button type="button" @click="copyText(payload.content, 'content')">{{ copied === 'content' ? 'Скопировано' : 'Копировать' }}</button></header>
      <pre class="event-code"><code>{{ payload.content }}</code></pre>
    </section>

    <section v-if="payload.output" class="event-section">
      <header><h3>Результат</h3><button type="button" @click="copyText(payload.output, 'output')">{{ copied === 'output' ? 'Скопировано' : 'Копировать' }}</button></header>
      <pre class="event-output"><code>{{ payload.output }}</code></pre>
    </section>

    <p v-if="!payload.path && !payload.command && !payload.content && !payload.before && !payload.output && !payload.task" class="event-fallback">
      {{ payload.detail || 'Подробности не переданы источником.' }}
    </p>

    <footer class="event-detail-footer">
      <time>{{ formattedTime }}</time>
      <span v-if="payload.exitCode != null">exit {{ payload.exitCode }}</span>
      <span v-if="formatDuration(payload.durationMs)">{{ formatDuration(payload.durationMs) }}</span>
      <span v-if="payload.tokensIn != null || payload.tokensOut != null">{{ payload.tokensIn || 0 }} → {{ payload.tokensOut || 0 }} токенов</span>
    </footer>
  </article>
</template>
