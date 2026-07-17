<script setup lang="ts">
import type { AssistantConnections, CrmDigest, CrmSession, SessionChannel } from '~/types/activity'

type ActivityTab = 'sessions' | 'report' | 'connections'
type SessionFilter = 'all' | SessionChannel

const route = useRoute()
const router = useRouter()
const tab = ref<ActivityTab>(
  route.query.tab === 'report' || route.query.tab === 'connections' ? route.query.tab : 'sessions',
)
const filter = ref<SessionFilter>('all')
const days = ref(7)
const digestUrl = computed(() => `/api/crm/digest?days=${days.value}`)

const {
  data: sessions,
  pending: sessionsPending,
  error: sessionsError,
  refresh: refreshSessions,
} = await useApi<CrmSession[]>('/api/crm/conversations')
const {
  data: digest,
  pending: digestPending,
  error: digestError,
  refresh: refreshDigest,
} = await useApi<CrmDigest>(digestUrl, { watch: [digestUrl] })
const {
  data: connections,
  pending: connectionsPending,
  error: connectionsError,
  refresh: refreshConnections,
} = await useApi<AssistantConnections>('/api/connections')

const filters: Array<{ value: SessionFilter; label: string }> = [
  { value: 'all', label: 'Все' },
  { value: 'local_cli', label: 'CLI' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'hereassistant_server', label: 'HereAssistant' },
  { value: 'crm_agent', label: 'CRM-agent' },
]

const visibleSessions = computed(() => {
  const items = sessions.value || []
  return filter.value === 'all' ? items : items.filter((session) => session.channel === filter.value)
})

function errorText(error: unknown): string | null {
  if (!error) return null
  const code = (error as any)?.data?.error
  if (code === 'crm_token_needs_read_scope') return 'Токен выпущен до появления чтения. Перевыпустите его в HereCRM — старый токен продолжает только отправлять сессии.'
  if (code === 'crm_not_configured') return 'HereCRM пока не подключена. Откройте «Подключения», чтобы закончить настройку.'
  if (code === 'crm_owner_only') return 'CRM-активность доступна только владельцу ассистента.'
  return 'Не удалось получить данные HereCRM. Проверьте подключение и повторите.'
}

function selectTab(next: ActivityTab) {
  tab.value = next
  void router.replace({ query: next === 'sessions' ? {} : { tab: next } })
}

function refreshCurrent() {
  if (tab.value === 'sessions') return refreshSessions()
  if (tab.value === 'report') return refreshDigest()
  return refreshConnections()
}

function openSession(id: string) {
  navigateTo(`/activity/${id}`)
}

watch(() => route.query.tab, (value) => {
  tab.value = value === 'report' || value === 'connections' ? value : 'sessions'
})
</script>

<template>
  <div class="activity-page">
    <header class="activity-header">
      <div>
        <div class="eyebrow">Личный ассистент</div>
        <h1>Активность</h1>
      </div>
      <button class="activity-refresh" type="button" aria-label="Обновить" @click="refreshCurrent">↻</button>
    </header>

    <nav class="activity-tabs" aria-label="Разделы активности">
      <button :class="tab === 'sessions' ? 'activity-tab-active' : ''" type="button" @click="selectTab('sessions')">Сессии</button>
      <button :class="tab === 'report' ? 'activity-tab-active' : ''" type="button" @click="selectTab('report')">Отчёт</button>
      <button :class="tab === 'connections' ? 'activity-tab-active' : ''" type="button" @click="selectTab('connections')">Подключения</button>
    </nav>

    <template v-if="tab === 'sessions'">
      <div class="filter-scroller" aria-label="Фильтр каналов">
        <button
          v-for="item in filters"
          :key="item.value"
          :class="filter === item.value ? 'filter-chip-active' : ''"
          type="button"
          @click="filter = item.value"
        >
          {{ item.label }}
        </button>
      </div>

      <div v-if="sessionsPending" class="activity-state">Загружаю личные сессии…</div>
      <div v-else-if="sessionsError" class="activity-state activity-state-error">
        <p>{{ errorText(sessionsError) }}</p>
        <button class="btn" type="button" @click="refreshSessions">Повторить</button>
      </div>
      <div v-else-if="visibleSessions.length" class="session-list">
        <ActivitySessionCard v-for="session in visibleSessions" :key="session.id" :session="session" />
      </div>
      <div v-else class="activity-empty">
        <div class="activity-empty-icon">⌁</div>
        <h2>Сессий в этом канале пока нет</h2>
        <p>Запустите ассистента через Telegram или CLI — новая активность появится здесь автоматически.</p>
      </div>

      <NuxtLink to="/history" class="local-history-link">
        <span><strong>Локальная история HereAssistant</strong><small>Диалоги, сохранённые на этом сервере</small></span>
        <b>›</b>
      </NuxtLink>
    </template>

    <ActivityReport
      v-else-if="tab === 'report'"
      :digest="digest || null"
      :pending="digestPending"
      :error="errorText(digestError)"
      :days="days"
      @period="days = $event"
      @open="openSession"
    />

    <ActivityConnections
      v-else
      :data="connections || null"
      :pending="connectionsPending"
      :error="errorText(connectionsError)"
    />

    <button class="new-session-cta" type="button" @click="selectTab('connections')">
      <span>＋</span> Новая сессия
    </button>
  </div>
</template>
