<script setup lang="ts">
import type { AssistantConnections } from '~/types/activity'
import { providerLabel } from '~/types/activity'

const props = defineProps<{
  data: AssistantConnections | null
  pending: boolean
  error: string | null
}>()

const copied = ref(false)

async function copyCommand() {
  if (!props.data?.cli.launchCommand) return
  await navigator.clipboard.writeText(props.data.cli.launchCommand)
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1600)
}
</script>

<template>
  <div v-if="pending" class="activity-state">Проверяю подключения…</div>
  <div v-else-if="error" class="activity-state activity-state-error">{{ error }}</div>
  <div v-else-if="data" class="space-y-3">
    <article class="connection-card">
      <div class="connection-card-icon connection-telegram">✦</div>
      <div class="min-w-0 flex-1">
        <div class="connection-card-title"><h3>Telegram-агент</h3><span :class="data.telegram.status === 'active' ? 'status-active' : ''">{{ data.telegram.status === 'active' ? 'Подключён' : 'Не настроен' }}</span></div>
        <p>Основной мобильный канал: голос, файлы, статусы и ответы агента.</p>
        <div v-if="data.telegram.user" class="connection-account">@{{ data.telegram.user.username || data.telegram.user.first_name || data.telegram.user.id }}</div>
      </div>
    </article>

    <article class="connection-card">
      <div class="connection-card-icon connection-cli">›_</div>
      <div class="min-w-0 flex-1">
        <div class="connection-card-title"><h3>Terminal CLI</h3><span :class="data.cli.status === 'active' ? 'status-active' : ''">{{ data.cli.status === 'active' ? 'Готов' : 'Нет профилей' }}</span></div>
        <p>Полный поток reasoning, tool calls и ответы без лимита Telegram.</p>
        <button class="command-copy" type="button" @click="copyCommand">
          <code>{{ data.cli.launchCommand }}</code><span>{{ copied ? 'Скопировано' : 'Копировать' }}</span>
        </button>
      </div>
      <div v-if="data.cli.accounts.length" class="connection-accounts-list">
        <div v-for="account in data.cli.accounts" :key="`${account.provider}:${account.label}`">
          <span class="status-orb status-orb-active" />
          <strong>{{ account.label }}</strong>
          <span>{{ providerLabel(account.provider) }}{{ account.defaultModel ? ` · ${account.defaultModel}` : '' }}</span>
        </div>
      </div>
    </article>

    <article class="connection-card">
      <div class="connection-card-icon connection-crm">CRM</div>
      <div class="min-w-0 flex-1">
        <div class="connection-card-title"><h3>HereCRM</h3><span :class="data.crm.status === 'active' ? 'status-active' : ''">{{ data.crm.status === 'active' ? 'Синхронизация активна' : 'Нужно подключить' }}</span></div>
        <p>CRM получает только явно разрешённые проектом данные. Обратная витрина доступна владельцу.</p>
        <div v-if="data.crm.status !== 'active'" class="connection-hint">
          Выпустите новый HereAssistant-токен в CRM и задайте `HERECRM_SYNC_URL` + `HERECRM_SYNC_TOKEN`.
        </div>
      </div>
    </article>

    <NuxtLink to="/settings" class="connection-git-link">
      <span class="connection-card-icon connection-git">Git</span>
      <span><strong>Git-пространство</strong><small>Подключить Gitea/GitHub и выбрать репозитории</small></span>
      <b>›</b>
    </NuxtLink>
  </div>
</template>
