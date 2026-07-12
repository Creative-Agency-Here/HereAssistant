<template>
  <div class="space-y-6">
    <header>
      <div class="text-text-soft text-xs uppercase tracking-wider">Статистика</div>
      <h1 class="text-2xl font-semibold mt-1">Экономия RTK</h1>
    </header>

    <div v-if="pending" class="card text-text-soft">Загрузка…</div>
    <div v-else-if="error" class="card text-err">Ошибка: {{ error.message }}</div>
    <div v-else-if="data" class="space-y-4">
      <div class="card grid grid-cols-2 gap-4">
        <div>
          <div class="text-text-dim text-xs">Сэкономлено</div>
          <div class="text-2xl font-semibold text-ok">{{ data.saved_tokens }}</div>
          <div class="text-text-soft text-sm">токенов контекста</div>
        </div>
        <div>
          <div class="text-text-dim text-xs">Эффективность</div>
          <div class="text-2xl font-semibold">{{ data.savings_pct }}%</div>
          <div class="text-text-soft text-sm">{{ data.commands }} команд</div>
        </div>
      </div>

      <div class="card space-y-2 text-sm">
        <div class="flex justify-between"><span class="text-text-soft">До RTK</span><span>{{ data.input_tokens }}</span></div>
        <div class="flex justify-between"><span class="text-text-soft">После RTK</span><span>{{ data.output_tokens }}</span></div>
        <div class="flex justify-between"><span class="text-text-soft">Сегодня</span><span>−{{ data.today_saved_tokens }}</span></div>
        <div class="flex justify-between"><span class="text-text-soft">Личных аккаунтов</span><span>{{ data.accounts }}</span></div>
      </div>

      <div class="text-xs text-text-dim">
        RTK сокращает вывод поддерживаемых shell-команд. Текст запросов и ответы модели в эту статистику не входят.
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
interface RtkSavings {
  available: boolean
  accounts: number
  commands: number
  input_tokens: number
  output_tokens: number
  saved_tokens: number
  savings_pct: number
  today_commands: number
  today_saved_tokens: number
}

const { data, pending, error } = await useApi<RtkSavings>('/api/rtk')
</script>
