<template>
  <div class="space-y-6">
    <header class="flex items-end gap-3 flex-wrap">
      <div>
        <div class="text-text-soft text-xs uppercase tracking-wider">Журнал</div>
        <h1 class="text-2xl font-semibold mt-1">Правки файлов</h1>
      </div>
      <input v-model="file"
             @input="onSearch"
             placeholder="фильтр по файлу…"
             class="ml-auto bg-bg-soft border border-line rounded-lg px-3 py-1.5
                    text-sm placeholder-text-dim focus:outline-none focus:border-accent/60"/>
    </header>

    <div v-if="filtered" class="card flex items-center gap-2 text-sm">
      <span class="text-text-soft">Показаны правки одного запроса.</span>
      <NuxtLink to="/edits" class="text-accent ml-auto">показать все →</NuxtLink>
    </div>

    <div v-if="pending" class="card text-text-soft">Загрузка…</div>
    <div v-else-if="error" class="card text-err">Ошибка: {{ error.message }}</div>
    <div v-else-if="!data?.items?.length" class="card text-text-soft">
      Правок пока нет. Они появятся после того, как бот что-нибудь отредактирует.
    </div>
    <ul v-else class="space-y-3">
      <li v-for="g in groups" :key="g.file" class="card">
        <!-- заголовок файла -->
        <div class="flex items-center gap-2 text-xs text-text-dim flex-wrap cursor-pointer select-none"
             @click="toggle(g.file)">
          <span>{{ open.has(g.file) ? '▾' : '▸' }}</span>
          <span class="font-mono text-text break-all">{{ g.name }}</span>
          <span>{{ g.edits.length }} {{ pluralEdits(g.edits.length) }}</span>
          <span class="text-ok">+{{ g.added }}</span>
          <span class="text-err">−{{ g.removed }}</span>
          <span class="ml-auto">{{ fmtTime(g.ts) }}</span>
          <span v-if="g.model" class="chip">{{ g.model }}</span>
        </div>
        <!-- диффы правок этого файла -->
        <div v-if="open.has(g.file)" class="mt-2 space-y-2">
          <div v-for="e in g.edits" :key="e.id">
            <div v-if="g.edits.length > 1" class="text-xs text-text-dim mb-1">
              {{ e.tool }} · <span class="text-ok">+{{ e.added }}</span>/<span class="text-err">−{{ e.removed }}</span> · {{ fmtTime(e.ts) }}
            </div>
            <div class="diff font-mono text-xs leading-relaxed rounded-lg overflow-x-auto border border-line bg-bg">
              <div v-for="(row, i) in parseDiff(e.diff)" :key="i"
                   :class="row.cls" class="flex whitespace-pre">
                <span class="w-9 shrink-0 px-1 text-right text-text-dim select-none">{{ row.oldNo }}</span>
                <span class="w-9 shrink-0 px-1 text-right text-text-dim select-none border-r border-line">{{ row.newNo }}</span>
                <span class="px-2">{{ row.text || ' ' }}</span>
              </div>
              <div v-if="!e.diff" class="px-3 py-1 text-text-dim">(дифф пуст)</div>
            </div>
          </div>
        </div>
      </li>
    </ul>

    <div v-if="!filtered && (hasMore || offset > 0)" class="flex justify-center gap-2 pt-2">
      <button class="btn" :disabled="offset === 0" @click="prev">← раньше</button>
      <button class="btn" :disabled="!hasMore" @click="next">позже →</button>
    </div>
  </div>
</template>

<script setup lang="ts">
interface ChangeItem {
  id: number; ts: number; thread_id: number
  account?: string; model?: string
  file: string; tool: string; added: number; removed: number; diff: string
}
interface ChangesResp { items: ChangeItem[]; limit: number; offset: number }

const route = useRoute()
const offset = ref(0)
const limit = 30
const file = ref('')
let searchTimer: any = null

// Фильтр по конкретному запросу бота: ?thread=&since=&until= (кнопка под ответом).
const filtered = computed(() => !!(route.query.since || route.query.until || route.query.thread))

const url = computed(() => {
  const p = new URLSearchParams({ limit: String(limit), offset: String(offset.value) })
  if (file.value) p.set('file', file.value)
  if (route.query.thread) p.set('thread', String(route.query.thread))
  if (route.query.since) p.set('since', String(route.query.since))
  if (route.query.until) p.set('until', String(route.query.until))
  return `/api/changes?${p}`
})

const { data, pending, error } = await useApi<ChangesResp>(url, { watch: [url] })
const hasMore = computed(() => (data.value?.items?.length || 0) === limit)

interface Group {
  file: string; name: string; edits: ChangeItem[]
  added: number; removed: number; ts: number; model?: string
}

// Группируем правки по файлу (в пределах загруженной страницы), порядок — по первому появлению.
const groups = computed<Group[]>(() => {
  const map = new Map<string, Group>()
  for (const e of data.value?.items || []) {
    let g = map.get(e.file)
    if (!g) {
      g = { file: e.file, name: e.file.split(/[\\/]/).pop() || e.file,
            edits: [], added: 0, removed: 0, ts: e.ts, model: e.model }
      map.set(e.file, g)
    }
    g.edits.push(e)
    g.added += e.added
    g.removed += e.removed
    g.ts = Math.max(g.ts, e.ts)
  }
  return [...map.values()]
})

// open хранит file-ключи раскрытых групп. По умолчанию раскрываем небольшие (≤60 строк суммарно).
const open = ref(new Set<string>())
watch(groups, (gs) => {
  const s = new Set<string>()
  for (const g of gs) {
    const lines = g.edits.reduce((n, e) => n + (e.diff || '').split('\n').length, 0)
    if (lines <= 60) s.add(g.file)
  }
  open.value = s
}, { immediate: true })

function toggle(file: string) {
  const s = new Set(open.value)
  s.has(file) ? s.delete(file) : s.add(file)
  open.value = s
}

function pluralEdits(n: number) {
  const d = n % 10, dd = n % 100
  if (d === 1 && dd !== 11) return 'правка'
  if (d >= 2 && d <= 4 && (dd < 10 || dd >= 20)) return 'правки'
  return 'правок'
}

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { offset.value = 0 }, 300)
}
function next() { offset.value += limit }
function prev() { offset.value = Math.max(0, offset.value - limit) }

// Парсит unified-дифф в строки с номерами (старый № / новый №), считая их от @@-заголовков.
function parseDiff(diff: string) {
  const rows: { cls: string; oldNo: number | string; newNo: number | string; text: string }[] = []
  let oldNo = 0, newNo = 0
  for (const ln of (diff || '').split('\n')) {
    if (ln.startsWith('@@')) {
      const m = ln.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
      if (m) { oldNo = parseInt(m[1]); newNo = parseInt(m[2]) }
      rows.push({ cls: 'text-accent', oldNo: '', newNo: '', text: ln })
    } else if (ln.startsWith('+++') || ln.startsWith('---')) {
      rows.push({ cls: 'text-text-dim', oldNo: '', newNo: '', text: ln })
    } else if (ln.startsWith('+')) {
      rows.push({ cls: 'text-ok bg-ok/10', oldNo: '', newNo: newNo++, text: ln })
    } else if (ln.startsWith('-')) {
      rows.push({ cls: 'text-err bg-err/10', oldNo: oldNo++, newNo: '', text: ln })
    } else {
      rows.push({ cls: 'text-text-soft', oldNo: oldNo++, newNo: newNo++, text: ln })
    }
  }
  return rows
}

function fmtTime(ts: number) {
  const d = new Date(ts * 1000)
  const now = new Date()
  const today = d.toDateString() === now.toDateString()
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  if (today) return `сегодня ${hh}:${mm}`
  return `${d.getDate()}.${(d.getMonth() + 1).toString().padStart(2, '0')} ${hh}:${mm}`
}
</script>
