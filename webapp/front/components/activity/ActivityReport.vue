<script setup lang="ts">
import type { CrmDigest } from '~/types/activity'

defineProps<{
  digest: CrmDigest | null
  pending: boolean
  error: string | null
  days: number
}>()

const emit = defineEmits<{ period: [days: number]; open: [id: string] }>()

function dateLabel(value: string | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' }).format(new Date(value))
}
</script>

<template>
  <div class="space-y-4">
    <div class="period-switch" role="group" aria-label="Период отчёта">
      <button :class="days === 7 ? 'period-active' : ''" type="button" @click="emit('period', 7)">7 дней</button>
      <button :class="days === 30 ? 'period-active' : ''" type="button" @click="emit('period', 30)">30 дней</button>
    </div>

    <div v-if="pending" class="activity-state">Собираю фактический отчёт…</div>
    <div v-else-if="error" class="activity-state activity-state-error">{{ error }}</div>
    <template v-else-if="digest">
      <section class="report-hero">
        <div>
          <div class="eyebrow">Личная работа</div>
          <div class="report-big-number">{{ digest.sessions.total }}</div>
          <p>AI-сессий за {{ digest.days }} дней</p>
        </div>
        <div class="report-ring"><span>{{ digest.sessions.local }}</span><small>CLI</small></div>
      </section>

      <div class="report-grid">
        <article class="metric-card">
          <span class="metric-icon">⌁</span>
          <strong>{{ digest.commits.total }}</strong>
          <span>коммитов команды</span>
          <small>{{ digest.commits.authors }} авторов</small>
        </article>
        <article class="metric-card">
          <span class="metric-icon">↥</span>
          <strong>{{ digest.deploys.total }}</strong>
          <span>деплоев команды</span>
          <small :class="digest.deploys.failed ? 'text-err' : 'text-ok'">
            {{ digest.deploys.failed ? `${digest.deploys.failed} с ошибкой` : 'без ошибок' }}
          </small>
        </article>
      </div>

      <section class="activity-panel">
        <div class="activity-panel-heading">
          <div><h3>Последние сессии</h3><p>Только ваши заголовки и проекты</p></div>
          <span>{{ digest.sessions.recent.length }}</span>
        </div>
        <button
          v-for="session in digest.sessions.recent"
          :key="session.id"
          class="report-session-row"
          type="button"
          @click="emit('open', session.id)"
        >
          <div class="min-w-0">
            <strong>{{ session.title || 'Сессия без названия' }}</strong>
            <span>{{ session.projectName || 'Без проекта' }}</span>
          </div>
          <time>{{ dateLabel(session.lastActivityAt) }}</time><b>›</b>
        </button>
        <div v-if="!digest.sessions.recent.length" class="activity-empty-small">За период сессий нет.</div>
      </section>

      <section v-if="digest.commits.byRepo.length" class="activity-panel">
        <div class="activity-panel-heading"><div><h3>Репозитории</h3><p>Командная активность</p></div></div>
        <div v-for="repo in digest.commits.byRepo" :key="repo.repo || 'unknown'" class="report-breakdown-row">
          <span>{{ repo.repo || 'Без репозитория' }}</span><strong>{{ repo.count }}</strong>
        </div>
      </section>
    </template>
  </div>
</template>
