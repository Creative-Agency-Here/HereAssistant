<template>
  <main class="auth-gate">
    <section class="auth-card" aria-live="polite">
      <div class="auth-brand">
        <span class="auth-mark"><GitHereGitMark /></span>
        <span>
          <strong>HereAssistant</strong>
          <small>Доступ через HereCRM</small>
        </span>
      </div>

      <div v-if="state === 'checking'" class="auth-copy">
        <span class="auth-spinner" aria-hidden="true" />
        <p class="auth-eyebrow">Проверяем доступ</p>
        <h1>Секунду, сверяем вашу сессию HereCRM</h1>
        <p>Пароль и CRM-токены не передаются ассистенту.</p>
      </div>

      <div v-else class="auth-copy">
        <span class="auth-status" :class="state === 'error' ? 'auth-status-error' : ''">
          {{ state === 'error' ? '!' : '↗' }}
        </span>
        <p class="auth-eyebrow">{{ eyebrow }}</p>
        <h1>{{ title }}</h1>
        <p>{{ description }}</p>

        <a v-if="crmLoginUrl" :href="crmLoginUrl" class="auth-primary">
          Перейти в HereCRM
          <span aria-hidden="true">→</span>
        </a>
        <button type="button" class="auth-secondary" @click="$emit('retry')">
          Проверить ещё раз
        </button>
      </div>

      <footer>
        <span class="status-orb status-orb-active" />
        Безопасный одноразовый вход
      </footer>
    </section>
  </main>
</template>

<script setup lang="ts">
const props = defineProps<{
  state: 'checking' | 'unauthorized' | 'denied' | 'error'
  crmLoginUrl: string
}>()

defineEmits<{ retry: [] }>()

const eyebrow = computed(() => {
  if (props.state === 'denied') return 'Нет доступа к этому ассистенту'
  if (props.state === 'error') return 'Не удалось проверить доступ'
  return 'Вы не авторизованы'
})

const title = computed(() => {
  if (props.state === 'denied') return 'Открыт другой аккаунт HereCRM'
  if (props.state === 'error') return 'HereCRM временно не ответил'
  return 'Сначала войдите в HereCRM'
})

const description = computed(() => {
  if (props.state === 'denied') {
    return 'Этот HereAssistant подключён к другому пользователю CRM. Войдите под владельцем подключения.'
  }
  if (props.state === 'error') {
    return 'Проверьте соединение и повторите попытку. Если ошибка останется, откройте HereCRM отдельно.'
  }
  return 'После входа CRM вернёт вас сюда, и ассистент откроется автоматически.'
})
</script>

<style scoped>
.auth-gate {
  @apply min-h-screen p-4 sm:p-8 flex items-center justify-center;
}
.auth-card {
  @apply w-full max-w-md overflow-hidden rounded-[2rem] border border-line bg-bg-card/95 p-5 sm:p-7;
  box-shadow: 0 28px 90px rgba(0, 0, 0, 0.36);
}
.auth-brand { @apply flex items-center gap-3 border-b border-line pb-5; }
.auth-brand > span:last-child { @apply flex min-w-0 flex-col; }
.auth-brand strong { @apply text-sm font-bold; }
.auth-brand small { @apply mt-0.5 text-[10px] uppercase tracking-[0.16em] text-text-dim; }
.auth-mark { @apply flex size-11 shrink-0 items-center justify-center border border-line bg-[#181818] p-2.5; }
.auth-mark :deep(svg) { @apply size-full; }
.auth-copy { @apply flex min-h-[21rem] flex-col items-center justify-center py-8 text-center; }
.auth-eyebrow { @apply mt-5 text-[10px] font-bold uppercase tracking-[0.18em] text-accent; }
.auth-copy h1 { @apply mt-2 text-2xl font-bold leading-tight tracking-tight; }
.auth-copy > p:last-of-type { @apply mt-3 max-w-sm text-sm leading-relaxed text-text-soft; }
.auth-spinner { @apply size-12 rounded-full border-2 border-line border-t-accent animate-spin; }
.auth-status { @apply flex size-12 items-center justify-center rounded-2xl border border-accent/25 bg-accent/10 text-xl text-accent; }
.auth-status-error { @apply border-err/25 bg-err/10 text-err; }
.auth-primary { @apply mt-7 flex min-h-12 w-full items-center justify-center gap-3 rounded-xl border border-accent bg-accent px-5 text-sm font-bold text-white transition hover:bg-accent-hover; box-shadow: 0 12px 34px rgba(171, 96, 246, 0.25); }
.auth-primary span { @apply text-lg; }
.auth-secondary { @apply mt-2 min-h-11 w-full rounded-xl px-4 text-sm font-medium text-text-soft transition hover:bg-bg-soft hover:text-text; }
.auth-card footer { @apply flex items-center justify-center gap-2 border-t border-line pt-4 text-[11px] text-text-dim; }
</style>
