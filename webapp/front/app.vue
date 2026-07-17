<template>
  <AuthBrowserAuthGate
    v-if="state !== 'authorized'"
    :state="state"
    :crm-login-url="crmLoginUrl"
    @retry="check"
  />
  <div v-else class="app-shell">
    <aside class="app-sidebar">
      <div class="brand-block">
        <div class="brand-mark brand-mark-logo"><GitHereGitMark /></div>
        <div class="min-w-0">
          <div class="brand-name">HereAssistant</div>
          <div class="brand-caption">ИИ-пространство</div>
        </div>
      </div>

      <div class="nav-caption">Рабочее пространство</div>
      <nav class="space-y-1">
        <NuxtLink to="/" class="nav-item" active-class="nav-item-active">
          <AppNavIcon name="now" />Сейчас
        </NuxtLink>
        <NuxtLink to="/activity" class="nav-item" active-class="nav-item-active">
          <AppNavIcon name="activity" />Активность
        </NuxtLink>
        <NuxtLink to="/edits" class="nav-item" active-class="nav-item-active">
          <AppNavIcon name="edits" />Правки
        </NuxtLink>
        <NuxtLink to="/stats" class="nav-item" active-class="nav-item-active">
          <AppNavIcon name="stats" />Экономия RTK
        </NuxtLink>
        <NuxtLink to="/settings" class="nav-item" active-class="nav-item-active">
          <AppNavIcon name="git" />Git-пространство
        </NuxtLink>
      </nav>
      <div class="sidebar-footer">
        <span class="status-orb bg-accent" />
        <span>Свой сервер</span>
        <span class="ml-auto text-right leading-tight text-text-dim">Приватность<br>прежде всего</span>
      </div>
    </aside>

    <main class="app-main">
      <div class="app-content" :class="activityDetail ? 'app-content-detail' : ''"><NuxtPage /></div>
    </main>

    <nav v-if="!activityDetail" class="mobile-tabs">
      <NuxtLink to="/" class="tab-item" active-class="tab-item-active"><AppNavIcon name="now" /><span>Сейчас</span></NuxtLink>
      <NuxtLink to="/activity" class="tab-item" active-class="tab-item-active"><AppNavIcon name="activity" /><span>Активность</span></NuxtLink>
      <NuxtLink to="/edits" class="tab-item" active-class="tab-item-active"><AppNavIcon name="edits" /><span>Правки</span></NuxtLink>
      <NuxtLink to="/stats" class="tab-item" active-class="tab-item-active"><AppNavIcon name="stats" /><span>RTK</span></NuxtLink>
      <NuxtLink to="/settings" class="tab-item" active-class="tab-item-active"><AppNavIcon name="git" /><span>Git</span></NuxtLink>
    </nav>
  </div>
</template>

<script setup lang="ts">
const route = useRoute()
const activityDetail = computed(() => /^\/activity\/[^/]+/.test(route.path))
const { state, crmLoginUrl, check } = useBrowserAuth()
</script>
