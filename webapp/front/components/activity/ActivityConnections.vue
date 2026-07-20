<script setup lang="ts">
import type { AssistantConnections } from '~/types/activity'
import { providerLabel } from '~/types/activity'

const props = defineProps<{
  data: AssistantConnections | null
  pending: boolean
  error: string | null
}>()

const copied = ref(false)

const contourState = {
  working: 'Работает',
  open: 'Открыт',
  closed: 'Закрыт',
} as const

const gitState = {
  changes: 'Есть незакоммиченные изменения',
  diverged: 'Ветка разошлась с remote',
  push_needed: 'Есть коммиты для push',
  pull_needed: 'Есть изменения для pull',
  synced: 'Синхронизировано',
} as const

const deployState = {
  deployed: 'Задеплоено',
  partial: 'Задеплоено частично',
  pending: 'Ожидает деплоя',
  unknown: 'Нет подтверждения деплоя',
} as const

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
    <section class="card">
      <div class="connection-card-title"><h3>Рабочие контуры</h3><span>{{ data.contours.length }}</span></div>
      <p class="mt-1 text-sm text-text-soft">Mac, сервер и другие установки сводятся по сессиям HereCRM. У удалённых контуров состояние оценивается по последней активности.</p>
      <div class="connection-accounts-list mt-3">
        <div v-for="contour in data.contours" :key="contour.id">
          <span class="status-orb" :class="contour.state !== 'closed' ? 'status-orb-active' : ''" />
          <strong>{{ contour.label }}{{ contour.local ? ' · этот контур' : '' }}</strong>
          <span>{{ contourState[contour.state] }} · {{ contour.sessions }} сессий</span>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="connection-card-title"><h3>Проекты и доставка</h3><span>{{ data.workspace.repositoriesOnDisk }} на диске</span></div>
      <div class="mt-3 grid grid-cols-2 gap-2 text-sm">
        <div class="rounded-xl border border-line p-3"><span class="text-text-dim">Задачи</span><strong class="block mt-1">{{ data.workspace.tasks.open }} в работе</strong></div>
        <div class="rounded-xl border border-line p-3"><span class="text-text-dim">Git-доступ</span><strong class="block mt-1">{{ data.workspace.git.repositories }} реп.</strong></div>
        <div class="rounded-xl border border-line p-3"><span class="text-text-dim">Текущая ветка</span><strong class="block mt-1">{{ data.workspace.git.current.branch || 'не Git-проект' }}</strong><small v-if="data.workspace.git.current.state" class="block text-text-dim mt-1">{{ gitState[data.workspace.git.current.state] }}</small></div>
        <div class="rounded-xl border border-line p-3"><span class="text-text-dim">Деплой</span><strong class="block mt-1">{{ deployState[data.workspace.deployment.state] }}</strong><small class="block text-text-dim mt-1">Свободно {{ data.workspace.disk.freeLabel }}</small></div>
      </div>
    </section>

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
