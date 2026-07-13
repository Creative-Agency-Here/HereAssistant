<template>
  <div class="space-y-6">
    <header>
      <div class="text-text-soft text-xs uppercase tracking-wider">Настройки</div>
      <h1 class="text-2xl font-semibold mt-1">Git-аккаунты</h1>
      <p class="text-sm text-text-soft mt-2 max-w-2xl">
        Подключите свою учётную запись Gitea, GitHub или другого доступного Git-сервиса.
        HereAssistant не использует общий токен владельца,
        а агент не видит ваши Git credentials.
      </p>
    </header>

    <div v-if="result === 'connected'" class="card border-ok/40 text-ok">
      Git-аккаунт подключён. Теперь можно выбрать доступные репозитории.
    </div>
    <div v-else-if="result === 'error'" class="card border-err/40 text-err">
      Подключение не завершено. Попробуйте ещё раз или обратитесь к администратору.
    </div>
    <div v-if="actionError" class="card border-err/40 text-err">{{ actionError }}</div>

    <section class="space-y-3">
      <div class="flex items-center gap-3">
        <h2 class="text-sm font-semibold">Подключённые аккаунты</h2>
        <span v-if="data" class="chip">{{ data.connections.length }}</span>
      </div>

      <div v-if="pending" class="card text-text-soft">Загрузка…</div>
      <div v-else-if="error" class="card text-err">Не удалось загрузить Git-настройки.</div>
      <div v-else-if="!data?.connections.length" class="card text-text-soft">
        Пока нет подключённых Git-аккаунтов.
      </div>
      <ul v-else class="space-y-3">
        <li v-for="connection in data.connections" :key="connection.id" class="card">
          <div class="flex items-start gap-3">
            <div class="w-10 h-10 rounded-xl bg-bg-soft border border-line flex items-center justify-center font-semibold">
              {{ connection.provider === 'gitea' ? 'GT' : 'GH' }}
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-medium">{{ providerLabel(connection.provider) }}</span>
                <span v-if="connection.external_login" class="text-sm text-text-soft">
                  {{ connection.external_login }}
                </span>
                <span class="text-xs" :class="statusClass(connection.status)">
                  ● {{ statusLabel(connection.status) }}
                </span>
              </div>
              <div class="text-sm text-text-soft mt-1 break-all">
                Сервер: {{ connection.host }}
              </div>
              <div v-if="connection.expires_at" class="text-xs text-text-dim mt-1">
                Доступ до {{ formatDate(connection.expires_at) }}
              </div>
            </div>
          </div>
          <div class="flex gap-2 mt-4 flex-wrap">
            <button v-if="connection.status === 'active'" class="btn" :disabled="busy" @click="toggleRepositories(connection.id)">
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
          <div v-if="openConnection === connection.id" class="mt-4 border-t border-line pt-4">
            <div v-if="repositoryLoading" class="text-sm text-text-soft">Загрузка репозиториев…</div>
            <div v-else-if="!repositories[connection.id]?.length" class="text-sm text-text-dim">
              Gitea не вернула доступных репозиториев. Переподключите аккаунт для обновления каталога.
            </div>
            <ul v-else class="space-y-2">
              <li v-for="repository in repositories[connection.id]" :key="repository.external_repository_id"
                  class="bg-bg-soft border border-line rounded-lg p-3 flex items-center gap-3">
                <div class="min-w-0 flex-1">
                  <div class="text-sm font-medium truncate">{{ repository.owner_name }}/{{ repository.repository_name }}</div>
                  <div class="text-xs text-text-dim mt-0.5">
                    {{ repository.permission }} · {{ repository.default_branch || 'default branch' }}
                  </div>
                </div>
                <button class="btn shrink-0"
                        :class="repository.enabled ? 'text-ok border-ok/40' : ''"
                        :disabled="busy"
                        @click="setRepository(connection.id, repository)">
                  {{ repository.enabled ? 'Разрешён' : 'Разрешить' }}
                </button>
              </li>
            </ul>
          </div>
        </li>
      </ul>
    </section>

    <section class="card space-y-4">
      <div>
        <h2 class="font-medium">Подключить Gitea</h2>
        <p class="text-sm text-text-soft mt-1">
          Вы войдёте на своём Gitea-сервере и подтвердите доступ. Пароль вводится только там.
        </p>
      </div>
      <div v-if="data?.available.length" class="flex gap-2 flex-wrap">
        <button v-for="item in data.available" :key="item.host" class="btn" :disabled="busy" @click="connect(item.host)">
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
interface GitRepository {
  external_repository_id: string
  owner_name: string
  repository_name: string
  default_branch: string | null
  permission: string
  enabled: boolean
}

const route = useRoute()
const router = useRouter()
const result = computed(() => String(route.query.git || ''))
const busy = ref(false)
const actionError = ref('')
const openConnection = ref<number | null>(null)
const repositoryLoading = ref(false)
const repositories = ref<Record<number, GitRepository[]>>({})
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
  repositoryLoading.value = true
  actionError.value = ''
  try {
    const response = await apiFetch<{ repositories: GitRepository[] }>(
      `/api/git/connections/${connectionId}/repositories`,
    )
    repositories.value = { ...repositories.value, [connectionId]: response.repositories }
  } catch {
    actionError.value = 'Не удалось загрузить список репозиториев.'
  } finally {
    repositoryLoading.value = false
  }
}

async function setRepository(connectionId: number, repository: GitRepository) {
  busy.value = true
  actionError.value = ''
  const method = repository.enabled ? 'DELETE' : 'POST'
  try {
    const response = await apiFetch<{ enabled: boolean }>(
      `/api/git/connections/${connectionId}/repositories/${encodeURIComponent(repository.external_repository_id)}/grant`,
      { method },
    )
    repository.enabled = response.enabled
  } catch {
    actionError.value = 'Не удалось изменить доступ к репозиторию.'
  } finally {
    busy.value = false
  }
}

function statusLabel(status: string) {
  return ({ active: 'подключён', pending: 'ожидает', expired: 'истёк', revoked: 'отключён', error: 'ошибка' } as Record<string, string>)[status] || status
}
function providerLabel(provider: string) {
  return ({ gitea: 'Gitea', github: 'GitHub', gitlab: 'GitLab' } as Record<string, string>)[provider.toLowerCase()] || provider
}
function statusClass(status: string) {
  return status === 'active' ? 'text-ok' : status === 'pending' ? 'text-warn' : 'text-err'
}
function formatDate(timestamp: number) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp * 1000))
}
</script>
