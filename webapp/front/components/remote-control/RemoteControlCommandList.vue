<script setup lang="ts">
// Локальный журнал команд /rc, поставленных ЭТИМ браузером (раздел «Ответ 9. UI»
// плана). У владельца НЕТ маршрута чтения статуса чужой/старой команды — он есть
// только у раннера под device access-токеном. Поэтому здесь честно показывается
// лишь то, что сервер вернул В ОТВЕТ на POST этой вкладки, без доисполнения
// статусов, которых бэк не отдаёт. Никакого v-html: commandType/status —
// фиксированные словари, promptPreview — пользовательский текст как текстовый узел.
import { computed } from 'vue'
import type {
  RcCommandStatus,
  RcCommandType,
  RemoteControlContext,
} from '~/composables/useRemoteControl'

const props = defineProps<{ rc: RemoteControlContext }>()

const TYPE_LABEL: Record<RcCommandType, string> = {
  prompt: 'Промпт',
  stop: 'Стоп',
  git_commit: 'Git commit',
  git_push: 'Git push',
}
const STATUS_LABEL: Record<RcCommandStatus, string> = {
  pending: 'в очереди',
  claimed: 'взято устройством',
  running: 'выполняется',
  succeeded: 'выполнено',
  failed: 'ошибка',
  cancelled: 'отменено',
  indeterminate: 'статус неизвестен',
}
const STATUS_TONE: Record<RcCommandStatus, string> = {
  pending: 'text-text-dim',
  claimed: 'text-accent',
  running: 'text-accent',
  succeeded: 'text-ok',
  failed: 'text-err',
  cancelled: 'text-text-dim',
  indeterminate: 'text-warn',
}

const items = computed(() => props.rc.queue.value)

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
</script>

<template>
  <div v-if="rc.publication.value" data-rc-command-list class="space-y-1">
    <p v-if="!items.length" class="text-xs text-text-dim">Команды ещё не отправлялись.</p>
    <ul v-else class="max-h-40 overflow-y-auto space-y-1">
      <li
        v-for="item in items"
        :key="item.id"
        class="flex items-start gap-1.5 rounded-lg bg-bg-soft border border-line px-2 py-1 text-xs"
      >
        <span class="font-semibold text-text-soft shrink-0">{{ TYPE_LABEL[item.commandType] }}</span>
        <span v-if="item.promptPreview" class="min-w-0 flex-1 truncate text-text-dim">
          {{ item.promptPreview }}
        </span>
        <span v-else class="min-w-0 flex-1" />
        <span
          v-if="!item.created"
          class="shrink-0 text-text-dim"
          title="Сервер вернул уже существующую команду по этому ключу идемпотентности"
        >
          повтор
        </span>
        <span class="shrink-0" :class="STATUS_TONE[item.status]">{{ STATUS_LABEL[item.status] }}</span>
        <span class="shrink-0 text-text-dim">{{ fmtTime(item.createdAt) }}</span>
      </li>
    </ul>
  </div>
</template>
