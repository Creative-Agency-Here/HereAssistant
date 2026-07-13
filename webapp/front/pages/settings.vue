<template>
  <div class="page-stack git-page">
    <header class="git-hero">
      <div class="git-hero-brand">
        <div class="git-hero-mark">
          <GitHereGitMark />
        </div>
        <div>
          <div class="eyebrow">Here Agency Git</div>
          <h1 class="page-title">Git-пространство</h1>
        </div>
      </div>
      <div class="header-status">
        <span class="status-orb" :class="data?.connections.some((item) => item.status === 'active') ? 'status-orb-active' : ''" />
        {{ data?.connections.some((item) => item.status === 'active') ? 'Git подключён' : 'Нет подключения' }}
      </div>
      <p class="page-description git-hero-description">
        Подключите свою учётную запись Gitea, GitHub или другого доступного Git-сервиса.
        HereAssistant не использует общий токен владельца,
        а агент не видит ваши Git credentials.
      </p>
    </header>

    <div v-if="result === 'connected'" class="notice notice-ok">
      Git-аккаунт подключён. Теперь можно выбрать доступные репозитории.
    </div>
    <div v-else-if="result === 'error'" class="notice notice-error">
      Подключение не завершено. Попробуйте ещё раз или обратитесь к администратору.
    </div>
    <div v-if="actionError" class="notice notice-error">{{ actionError }}</div>

    <section class="space-y-4">
      <div class="section-heading">
        <div>
          <h2>Подключённые сервисы</h2>
          <p>Личные аккаунты и доступные агенту репозитории</p>
        </div>
        <span v-if="data" class="count-badge">{{ data.connections.length }}</span>
      </div>

      <div v-if="pending" class="card text-text-soft">Загрузка…</div>
      <div v-else-if="error" class="card text-err">Не удалось загрузить Git-настройки.</div>
      <div v-else-if="!data?.connections.length" class="card text-text-soft">
        Пока нет подключённых Git-аккаунтов.
      </div>
      <ul v-else class="space-y-3">
        <li v-for="connection in data.connections" :key="connection.id" class="integration-card">
          <div class="integration-summary">
            <div class="provider-mark" :class="connection.provider === 'gitea' ? 'provider-mark-gitea' : ''">
              <GitHereGitMark v-if="connection.provider === 'gitea'" />
              <span v-else>{{ providerMonogram(connection.provider) }}</span>
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-semibold">{{ providerLabel(connection.provider) }}</span>
                <span class="connection-status" :class="statusClass(connection.status)">
                  <span class="status-dot" />{{ statusLabel(connection.status) }}
                </span>
              </div>
              <div class="text-sm text-text-soft mt-1 break-all">
                {{ connection.external_login || 'профиль без логина' }} · {{ connection.host }}
              </div>
              <div v-if="connection.expires_at" class="text-xs text-text-dim mt-1">
                Доступ до {{ formatDate(connection.expires_at) }}
              </div>
            </div>
          </div>
          <div class="integration-actions">
            <button v-if="connection.status === 'active'" class="btn btn-primary" :disabled="busy" @click="toggleRepositories(connection.id)">
              {{ openConnection === connection.id ? 'Скрыть репозитории' : 'Выбрать репозитории' }}
            </button>
            <button v-if="connection.status === 'expired'" class="btn" :disabled="busy" @click="refreshAccess(connection)">
              Обновить доступ
            </button>
            <button v-if="connection.status !== 'active'" class="btn" :disabled="busy" @click="connect(connection.host)">
              Подключить снова
            </button>
            <button class="btn btn-danger" :disabled="busy" @click="revoke(connection)">
              Отключить
            </button>
          </div>
          <div v-if="openConnection === connection.id" class="integration-repositories">
            <GitRepositoryPicker :connection-id="connection.id" />
          </div>
        </li>
      </ul>
    </section>

    <section class="integration-card git-connect-card p-5 space-y-4">
      <div class="git-connect-heading">
        <div class="git-connect-mark"><GitHereGitMark /></div>
        <div>
          <h2 class="font-medium">Подключить Gitea</h2>
          <p class="text-sm text-text-soft mt-1">
            Вы войдёте на своём Gitea-сервере и подтвердите доступ. Пароль вводится только там.
          </p>
        </div>
      </div>
      <div v-if="data?.available.length" class="flex gap-2 flex-wrap">
        <button v-for="item in data.available" :key="item.host" class="btn btn-primary" :disabled="busy" @click="connect(item.host)">
          <span class="w-2 h-2 rounded-full bg-ok" />
          {{ item.host }}
        </button>
      </div>
      <div v-else class="text-sm text-text-dim">
        Администратор ещё не настроил public OAuth application для разрешённого Gitea host.
      </div>
      <div class="text-xs text-text-dim border-t border-line pt-3">
        Токен хранится в отдельном encrypted vault Git runner-а. Он не записывается в SQLite,
        историю диалогов, проект или RTK.
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
interface GitConnection {
  id: number
  provider: string
  host: string
  external_login: string | null
  status: string
  expires_at: number | null
}
interface GitSettings {
  connections: GitConnection[]
  available: { provider: string; host: string }[]
}
interface OAuthStart { connection_id: number; authorization_url: string }
const route = useRoute()
const router = useRouter()
const result = computed(() => String(route.query.git || ''))
const busy = ref(false)
const actionError = ref('')
const openConnection = ref<number | null>(null)
const { data, pending, error, refresh } = await useApi<GitSettings>('/api/git/connections')

onMounted(() => {
  window.Telegram?.WebApp?.ready?.()
  window.Telegram?.WebApp?.expand?.()
})

async function connect(host: string) {
  busy.value = true
  actionError.value = ''
  try {
    const response = await apiFetch<OAuthStart>('/api/git/connections/start', {
      method: 'POST',
      body: { provider: 'gitea', host },
    })
    window.location.assign(response.authorization_url)
  } catch {
    actionError.value = 'Не удалось начать подключение. Проверьте настройки Gitea OAuth.'
    busy.value = false
  }
}

async function revoke(connection: GitConnection) {
  if (!window.confirm(`Отключить ${connection.external_login || connection.host}?`)) return
  busy.value = true
  actionError.value = ''
  try {
    await apiFetch(`/api/git/connections/${connection.id}`, { method: 'DELETE' })
    await refresh()
    if (result.value) await router.replace({ path: '/settings' })
  } catch {
    actionError.value = 'Не удалось завершить отключение. Обновите страницу и повторите.'
    await refresh()
  } finally {
    busy.value = false
  }
}

async function refreshAccess(connection: GitConnection) {
  busy.value = true
  actionError.value = ''
  try {
    await apiFetch(`/api/git/connections/${connection.id}/refresh`, { method: 'POST' })
    await refresh()
  } catch {
    actionError.value = 'Автоматическое обновление недоступно. Подключите аккаунт снова.'
  } finally {
    busy.value = false
  }
}

async function toggleRepositories(connectionId: number) {
  if (openConnection.value === connectionId) {
    openConnection.value = null
    return
  }
  openConnection.value = connectionId
  actionError.value = ''
}

function statusLabel(status: string) {
  return ({ active: 'подключён', pending: 'ожидает', expired: 'истёк', revoked: 'отключён', error: 'ошибка' } as Record<string, string>)[status] || status
}
function providerLabel(provider: string) {
  return ({ gitea: 'Gitea', github: 'GitHub', gitlab: 'GitLab' } as Record<string, string>)[provider.toLowerCase()] || provider
}
function providerMonogram(provider: string) {
  return ({ github: 'GH', gitlab: 'GL' } as Record<string, string>)[provider.toLowerCase()] || provider.slice(0, 2).toUpperCase()
}
function statusClass(status: string) {
  return status === 'active' ? 'text-ok' : status === 'pending' ? 'text-warn' : 'text-err'
}
function formatDate(timestamp: number) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp * 1000))
}
</script>
