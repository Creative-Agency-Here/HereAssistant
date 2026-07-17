<script setup lang="ts">
import type { CrmSession } from '~/types/activity'
import { channelLabel, providerLabel } from '~/types/activity'

const props = defineProps<{ session: CrmSession }>()

function relativeTime(value: string | null) {
  if (!value) return '—'
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) return 'сейчас'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}м`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}ч`
  return `${Math.floor(seconds / 86400)}д`
}
</script>

<template>
  <button class="session-card" type="button" @click="navigateTo(`/activity/${props.session.id}`)">
    <div class="session-card-top">
      <span class="session-provider-orb" :class="`session-provider-${props.session.accountProvider || 'ai'}`">
        {{ providerLabel(props.session.accountProvider).slice(0, 1) }}
      </span>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <h3 class="session-card-title">{{ props.session.title || 'Сессия без названия' }}</h3>
          <time class="ml-auto shrink-0 text-sm text-text-dim">{{ relativeTime(props.session.lastActivityAt) }}</time>
        </div>
        <div class="session-connection-line">
          <span class="connection-glyph" aria-hidden="true">▱</span>
          <span class="text-ok">{{ channelLabel(props.session.channel) }}</span>
          <span>·</span>
          <span class="truncate">{{ props.session.projectName || props.session.cwd || 'Без проекта' }}</span>
        </div>
      </div>
    </div>
    <div class="session-card-preview">
      <span>{{ providerLabel(props.session.accountProvider) }}</span>
      <span v-if="props.session.model">· {{ props.session.model }}</span>
      <span v-if="props.session.ownerName || props.session.ownerUsername">
        · {{ props.session.ownerName || props.session.ownerUsername }}
      </span>
    </div>
  </button>
</template>
