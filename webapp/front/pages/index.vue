<template>
  <div class="space-y-6">
    <header>
      <div class="text-text-soft text-xs uppercase tracking-wider">Сейчас</div>
      <h1 class="text-2xl font-semibold mt-1">Текущая задача</h1>
    </header>

    <div v-if="pending && !data" class="card text-text-soft">Загрузка…</div>
    <div v-else-if="error && !data" class="card text-err">
      Ошибка: {{ error.message || 'не удалось получить статус' }}
    </div>

    <template v-else-if="data">
      <!-- статус -->
      <div class="card">
        <div class="flex items-center gap-2 mb-3">
          <span :class="['w-2 h-2 rounded-full',
                          data.active ? 'bg-ok animate-pulse' : 'bg-text-dim']" />
          <span class="text-sm text-text-soft">
            {{ data.active ? 'выполняется' : 'свободен' }}
          </span>
          <span v-if="data.active" class="ml-auto chip">{{ data.elapsed_sec }}с</span>
        </div>

        <div v-if="data.active" class="space-y-2">
          <div class="text-lg">{{ data.current_step }}</div>
          <div class="flex flex-wrap gap-2 text-xs">
            <span class="chip">🤖 {{ data.model }}</span>
            <span class="chip">👤 {{ data.account }}</span>
            <span class="chip">📁 {{ data.project }}</span>
          </div>
          <div class="flex gap-2 pt-2">
            <button class="btn btn-danger" :disabled="stopping" @click="onStop">
              {{ stopping ? 'Прерываю…' : 'Прервать' }}
            </button>
          </div>
        </div>
        <div v-else class="text-text-soft">Нет активных задач.</div>
      </div>

      <!-- последние действия -->
      <div class="card" v-if="data.recent_actions?.length">
        <div class="text-text-soft text-xs uppercase tracking-wider mb-2">
          Последние действия
        </div>
        <ul class="space-y-1.5 font-mono text-sm">
          <li v-for="(a, i) in data.recent_actions" :key="i"
              class="text-text-soft">
            <span class="text-text-dim mr-2">{{ data.recent_actions.length - i }}.</span>{{ a }}
          </li>
        </ul>
      </div>

      <!-- realtime лог -->
      <div class="card">
        <div class="flex items-center mb-2">
          <div class="text-text-soft text-xs uppercase tracking-wider">Лог бота</div>
          <span class="ml-auto text-xs"
                :class="connected ? 'text-ok' : 'text-text-dim'">
            ● {{ connected ? 'live' : 'offline' }}
          </span>
        </div>
        <div ref="logEl"
             class="font-mono text-xs text-text-soft bg-bg max-h-64 overflow-y-auto
                    border border-line rounded-lg p-2 space-y-0.5">
          <div v-for="(line, i) in logLines" :key="i"
               :class="lineClass(line)" class="whitespace-pre-wrap">
            {{ line }}
          </div>
          <div v-if="!logLines.length" class="text-text-dim">подключаюсь к стриму…</div>
        </div>
      </div>

      <!-- кто я (для отладки) -->
      <div class="text-xs text-text-dim">
        вошёл как: {{ data.user?.first_name }} (#{{ data.user?.id }})
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
interface NowResponse {
  active: boolean
  account: string
  model: string
  project: string
  current_step: string
  started_at: number
  elapsed_sec: number
  recent_actions: string[]
  user: { id: number; first_name: string; username?: string }
}

const { data, pending, error, refresh } = await useApi<NowResponse>('/api/now')
const { logLines, connected } = useLiveLog()
const logEl = ref<HTMLElement | null>(null)
const stopping = ref(false)

// автообновление статуса каждые 2 секунды (ws тоже шлёт, но fetch гарантирует upd)
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  timer = setInterval(() => refresh(), 2000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})

// автоскролл вниз когда приходят новые строки
watch(logLines, async () => {
  await nextTick()
  if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
})

async function onStop() {
  if (stopping.value) return
  stopping.value = true
  try {
    await apiFetch('/api/control/stop', { method: 'POST' })
    await new Promise(resolve => window.setTimeout(resolve, 600))
    await refresh()
  } catch {
    alert('Не удалось передать команду остановки. Проверьте подключение к HereAssistant API.')
  } finally {
    stopping.value = false
  }
}

function lineClass(line: string) {
  if (/\bERROR\b|Traceback/.test(line)) return 'text-err'
  if (/\bWARNING\b/.test(line)) return 'text-warn'
  if (/\bINFO\b/.test(line)) return 'text-text-soft'
  return ''
}
</script>
