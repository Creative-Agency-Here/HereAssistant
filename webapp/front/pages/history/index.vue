<template>
  <div class="space-y-6">
    <header class="flex items-end gap-3 flex-wrap">
      <div>
        <div class="text-text-soft text-xs uppercase tracking-wider">История</div>
        <h1 class="text-2xl font-semibold mt-1">Диалоги</h1>
      </div>
      <input v-model="q"
             @input="onSearch"
             placeholder="поиск по тексту запроса…"
             class="ml-auto bg-bg-soft border border-line rounded-lg px-3 py-1.5
                    text-sm placeholder-text-dim focus:outline-none focus:border-accent/60"/>
    </header>

    <div v-if="pending" class="card text-text-soft">Загрузка…</div>
    <div v-else-if="error" class="card text-err">
      Ошибка: {{ error.message }}
    </div>
    <div v-else-if="!data?.items?.length" class="card text-text-soft">
      Ничего не найдено.
    </div>
    <ul v-else class="space-y-2">
      <li v-for="c in data.items" :key="c.id"
          class="card hover:border-accent/40 cursor-pointer transition"
          @click="open(c)">
        <div class="flex items-center gap-2 mb-2 text-xs text-text-dim">
          <span class="chip">#{{ c.id }}</span>
          <span class="chip">{{ c.model || '—' }}</span>
          <span class="chip">{{ c.account || '—' }}</span>
          <span class="ml-auto">{{ fmtTime(c.updated_at) }}</span>
          <span class="chip">{{ c.msg_count }} сообщ.</span>
        </div>
        <div class="text-sm line-clamp-2">{{ c.last_user || '(нет сообщений)' }}</div>
      </li>
    </ul>

    <div class="flex justify-center gap-2 pt-2">
      <button class="btn" :disabled="offset === 0" @click="prev">← раньше</button>
      <button class="btn" :disabled="!hasMore" @click="next">позже →</button>
    </div>
  </div>
</template>

<script setup lang="ts">
interface ConvItem {
  id: number; chat_id: number; thread_id: number
  model?: string; project_name?: string; cwd?: string
  account?: string; last_user?: string; msg_count: number
  created_at: number; updated_at: number
}
interface HistResp { items: ConvItem[]; limit: number; offset: number }

const offset = ref(0)
const limit = 20
const q = ref('')
let searchTimer: any = null

const url = computed(() => {
  const p = new URLSearchParams({ limit: String(limit), offset: String(offset.value) })
  if (q.value) p.set('q', q.value)
  return `/api/history?${p}`
})

const { data, pending, error, refresh } = await useApi<HistResp>(url, { watch: [url] })

const hasMore = computed(() => (data.value?.items?.length || 0) === limit)

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { offset.value = 0 }, 300)
}
function next() { offset.value += limit }
function prev() { offset.value = Math.max(0, offset.value - limit) }

function fmtTime(ts: number) {
  const d = new Date(ts * 1000)
  const now = new Date()
  const today = d.toDateString() === now.toDateString()
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  if (today) return `сегодня ${hh}:${mm}`
  return `${d.getDate()}.${(d.getMonth() + 1).toString().padStart(2, '0')} ${hh}:${mm}`
}

function open(c: ConvItem) {
  navigateTo(`/history/${c.id}`)
}
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
