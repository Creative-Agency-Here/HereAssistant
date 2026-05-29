<template>
  <div class="space-y-6">
    <header class="flex items-center gap-3">
      <NuxtLink to="/history" class="btn">← История</NuxtLink>
      <div class="text-text-soft text-xs uppercase tracking-wider">Диалог</div>
      <span class="chip">#{{ id }}</span>
    </header>

    <div v-if="pending" class="card text-text-soft">Загрузка…</div>
    <div v-else-if="error" class="card text-err">
      Ошибка: {{ error.message }}
    </div>
    <template v-else-if="data">
      <!-- мета -->
      <div class="card">
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <span class="chip">🤖 {{ data.model || '—' }}</span>
          <span class="chip">👤 {{ data.account || '—' }}</span>
          <span v-if="data.project_name" class="chip">📁 {{ data.project_name }}</span>
          <span v-if="data.cwd" class="chip">{{ data.cwd }}</span>
          <span class="ml-auto text-text-dim">создан {{ fmt(data.created_at) }} · обновлён {{ fmt(data.updated_at) }}</span>
        </div>
      </div>

      <!-- сообщения -->
      <ul class="space-y-3">
        <li v-for="m in data.messages" :key="m.id"
            class="card"
            :class="m.role === 'user' ? 'border-accent/40' : ''">
          <div class="flex items-center text-xs text-text-dim mb-2">
            <span :class="m.role === 'user' ? 'text-accent' : 'text-text-soft'"
                  class="font-semibold uppercase">
              {{ m.role === 'user' ? 'Запрос' : 'Ответ' }}
            </span>
            <span class="ml-auto">{{ fmt(m.created_at) }}</span>
            <span v-if="m.model" class="chip ml-2">{{ m.model }}</span>
          </div>
          <div class="md" v-html="renderMarkdown(m.content)"></div>
        </li>
      </ul>

      <div v-if="!data.messages?.length" class="card text-text-soft">
        В этом диалоге ещё нет сообщений.
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
interface Message {
  id: number; role: 'user' | 'assistant'; content: string
  model?: string; provider?: string; created_at: number
}
interface Conv {
  id: number; chat_id: number; thread_id: number
  model?: string; account?: string; project_name?: string; cwd?: string
  created_at: number; updated_at: number
  messages: Message[]
}

const route = useRoute()
const id = computed(() => Number(route.params.id))

const { data, pending, error } = await useApi<Conv>(`/api/history/${id.value}`, {
  watch: [id],
})

function fmt(ts: number) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  return `${d.getDate()}.${(d.getMonth() + 1).toString().padStart(2, '0')} ${hh}:${mm}`
}
</script>
