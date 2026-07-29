<script setup lang="ts">
// Бейдж устройства режима /rc (раздел «Ответ 7. Индикация устройства» плана
// RC_REMOTE_CONTROL_PLAN.md): название, тип, состояние и свежесть heartbeat.
// Рендерится только когда для устройства ЕСТЬ публикация — иначе это шум на
// обычных (не /rc) сессиях. Названия устройств (MacBook / сервер DE / сервер
// Dell) задаёт владелец; WebApp их не переизобретает и получает сверху.
import { computed } from 'vue'
import type {
  RcCapabilities,
  RcDeviceBadgeState,
  RcPublication,
  RemoteControlContext,
} from '~/composables/useRemoteControl'

const props = withDefaults(
  defineProps<{
    rc: RemoteControlContext
    deviceName?: string
    deviceKind?: 'laptop' | 'server' | 'bot'
  }>(),
  { deviceName: '', deviceKind: 'laptop' },
)

const BADGE_LABEL: Record<RcDeviceBadgeState, string> = {
  online: 'Онлайн',
  busy_local: 'Занято локально',
  busy_remote: 'Занято удалённо',
  awaiting_approval: 'Ждёт подтверждения',
  offline: 'Офлайн',
  revoked: 'Отозвано',
  unpublished: 'Нет публикации',
}
const BADGE_DOT: Record<RcDeviceBadgeState, string> = {
  online: 'bg-ok',
  busy_local: 'bg-warn',
  busy_remote: 'bg-accent',
  awaiting_approval: 'bg-accent',
  offline: 'bg-text-dim',
  revoked: 'bg-err',
  unpublished: 'bg-text-dim',
}
const KIND_ICON: Record<'laptop' | 'server' | 'bot', string> = {
  laptop: '💻',
  server: '🖥',
  bot: '🤖',
}

const publication = computed<RcPublication | null>(() => props.rc.publication.value)
const badgeState = computed<RcDeviceBadgeState>(() => props.rc.badgeState.value)
const label = computed(() => BADGE_LABEL[badgeState.value])
const dotClass = computed(() => BADGE_DOT[badgeState.value])
const icon = computed(() => KIND_ICON[props.deviceKind])
const deviceLabel = computed(() => props.deviceName || 'Неизвестное устройство')

function relativeHeartbeat(iso: string): string {
  const ms = Date.now() - Date.parse(iso)
  if (Number.isNaN(ms) || ms < 0) return 'только что'
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec} с назад`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} мин назад`
  const hours = Math.floor(min / 60)
  if (hours < 24) return `${hours} ч назад`
  return `${Math.floor(hours / 24)} дн назад`
}
// Зависимость от deviceOnline (обновляется тиком каждые 5 с) заставляет превью
// свежести heartbeat пересчитываться без собственного таймера.
const heartbeatRelative = computed(() => {
  void props.rc.deviceOnline.value
  const pub = publication.value
  return pub ? relativeHeartbeat(pub.lastHeartbeatAt) : null
})

// Только реально заявленные capabilities; при private — presence-only, чипов нет.
const capabilityChips = computed(() => {
  const caps: RcCapabilities | null = publication.value?.capabilities ?? null
  if (publication.value?.privacyMode === 'private' || !caps) return []
  const chips: string[] = []
  if (caps.remotePrompt) chips.push('prompt')
  if (caps.stop) chips.push('stop')
  if (caps.gitCommit || caps.gitPush) chips.push('git')
  if (caps.toolEvents) chips.push('события')
  return chips
})
const isPresenceOnly = computed(() => publication.value?.privacyMode === 'private')
</script>

<template>
  <div
    v-if="publication"
    data-rc-device-badge
    class="inline-flex flex-wrap items-center gap-1.5 rounded-xl border border-line bg-bg-soft px-2.5 py-1.5 text-xs"
  >
    <span class="shrink-0" aria-hidden="true">{{ icon }}</span>
    <span class="font-semibold text-text truncate max-w-[160px]">{{ deviceLabel }}</span>
    <span class="inline-flex items-center gap-1 shrink-0">
      <span class="size-1.5 rounded-full" :class="dotClass" />
      <span class="text-text-soft">{{ label }}</span>
    </span>
    <span v-if="heartbeatRelative" class="text-text-dim shrink-0">· {{ heartbeatRelative }}</span>
    <span v-if="isPresenceOnly" class="text-text-dim shrink-0">· только присутствие</span>
    <span v-for="chip in capabilityChips" :key="chip" class="chip shrink-0">{{ chip }}</span>
  </div>
</template>
