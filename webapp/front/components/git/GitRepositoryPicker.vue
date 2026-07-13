<script setup lang="ts">
interface GitRepository {
  external_repository_id: string
  owner_name: string
  repository_name: string
  default_branch: string | null
  permission: string
  enabled: boolean
}

const props = defineProps<{ connectionId: number }>()

const query = ref('')
const loading = ref(true)
const busy = ref(false)
const errorText = ref('')
const repositories = ref<GitRepository[]>([])
const selectedIds = ref<string[]>([])

const filteredRepositories = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase('ru-RU')
  if (!needle) return repositories.value
  return repositories.value.filter((repository) =>
    [repository.owner_name, repository.repository_name, repository.default_branch || '']
      .join(' ')
      .toLocaleLowerCase('ru-RU')
      .includes(needle),
  )
})
const selectedSet = computed(() => new Set(selectedIds.value))
const allFilteredSelected = computed(
  () => filteredRepositories.value.length > 0
    && filteredRepositories.value.every((repository) => selectedSet.value.has(repository.external_repository_id)),
)
const enabledCount = computed(() => repositories.value.filter((repository) => repository.enabled).length)

onMounted(loadRepositories)

async function loadRepositories() {
  loading.value = true
  errorText.value = ''
  try {
    const response = await apiFetch<{ repositories: GitRepository[] }>(
      `/api/git/connections/${props.connectionId}/repositories`,
    )
    repositories.value = response.repositories
  } catch {
    errorText.value = 'Не удалось загрузить репозитории.'
  } finally {
    loading.value = false
  }
}

function toggleRepository(repositoryId: string) {
  selectedIds.value = selectedSet.value.has(repositoryId)
    ? selectedIds.value.filter((value) => value !== repositoryId)
    : [...selectedIds.value, repositoryId]
}

function toggleFiltered() {
  const filteredIds = filteredRepositories.value.map((repository) => repository.external_repository_id)
  if (allFilteredSelected.value) {
    const filteredSet = new Set(filteredIds)
    selectedIds.value = selectedIds.value.filter((value) => !filteredSet.has(value))
    return
  }
  selectedIds.value = [...new Set([...selectedIds.value, ...filteredIds])]
}

async function applyBulk(enabled: boolean) {
  if (!selectedIds.value.length || busy.value) return
  busy.value = true
  errorText.value = ''
  try {
    const response = await apiFetch<{ repository_ids: string[] }>(
      `/api/git/connections/${props.connectionId}/repositories`,
      {
        method: 'PATCH',
        body: { repository_ids: selectedIds.value, enabled },
      },
    )
    const changed = new Set(response.repository_ids)
    repositories.value = repositories.value.map((repository) =>
      changed.has(repository.external_repository_id) ? { ...repository, enabled } : repository,
    )
    selectedIds.value = []
  } catch {
    errorText.value = 'Не удалось изменить доступ. Список не был применён.'
  } finally {
    busy.value = false
  }
}

function permissionLabel(permission: string) {
  return ({ admin: 'администратор', write: 'запись', read: 'чтение' } as Record<string, string>)[permission] || permission
}
</script>

<template>
  <div class="repository-picker">
    <div class="repository-toolbar">
      <label class="search-field">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
        </svg>
        <input v-model="query" type="search" placeholder="Название, владелец или ветка…">
      </label>
      <div class="text-xs text-text-dim">
        Разрешено {{ enabledCount }} из {{ repositories.length }}
      </div>
    </div>

    <div v-if="loading" class="repository-state">Загружаем репозитории…</div>
    <div v-else-if="errorText && !repositories.length" class="repository-state text-err">
      {{ errorText }}
    </div>
    <div v-else-if="!repositories.length" class="repository-state">
      Gitea не вернула доступных репозиториев. Переподключите аккаунт для обновления каталога.
    </div>
    <template v-else>
      <div class="bulk-bar">
        <button type="button" class="bulk-select" @click="toggleFiltered">
          <span class="check-box" :class="allFilteredSelected ? 'check-box-active' : ''">
            <svg v-if="allFilteredSelected" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="m3 8 3 3 7-7" />
            </svg>
          </span>
          {{ allFilteredSelected ? 'Снять найденные' : 'Выбрать найденные' }}
        </button>
        <span v-if="selectedIds.length" class="selection-count">Выбрано {{ selectedIds.length }}</span>
        <div v-if="selectedIds.length" class="ml-auto flex gap-2">
          <button type="button" class="btn btn-quiet" :disabled="busy" @click="applyBulk(false)">
            Запретить
          </button>
          <button type="button" class="btn btn-primary" :disabled="busy" @click="applyBulk(true)">
            {{ busy ? 'Применяем…' : 'Разрешить' }}
          </button>
        </div>
      </div>

      <div v-if="errorText" class="px-4 py-2 text-xs text-err border-b border-line">
        {{ errorText }}
      </div>
      <div v-if="!filteredRepositories.length" class="repository-state">
        По запросу «{{ query }}» ничего не найдено.
      </div>
      <ul v-else class="repository-list">
        <li v-for="repository in filteredRepositories" :key="repository.external_repository_id">
          <button type="button" class="repository-row" @click="toggleRepository(repository.external_repository_id)">
            <span class="check-box" :class="selectedSet.has(repository.external_repository_id) ? 'check-box-active' : ''">
              <svg v-if="selectedSet.has(repository.external_repository_id)" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2">
                <path d="m3 8 3 3 7-7" />
              </svg>
            </span>
            <span class="repo-mark">{{ repository.owner_name.slice(0, 1).toUpperCase() }}</span>
            <span class="min-w-0 flex-1 text-left">
              <span class="block text-sm font-semibold truncate">
                {{ repository.owner_name }}/{{ repository.repository_name }}
              </span>
              <span class="block mt-0.5 text-xs text-text-dim truncate">
                {{ permissionLabel(repository.permission) }} · {{ repository.default_branch || 'ветка не задана' }}
              </span>
            </span>
            <span class="access-badge" :class="repository.enabled ? 'access-badge-active' : ''">
              <span class="status-dot" />
              {{ repository.enabled ? 'разрешён' : 'не выбран' }}
            </span>
          </button>
        </li>
      </ul>
    </template>
  </div>
</template>
