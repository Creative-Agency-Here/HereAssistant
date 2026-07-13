<template>
  <div class="app-shell">
    <aside class="app-sidebar">
      <div class="brand-block">
        <div class="brand-mark">H</div>
        <div class="min-w-0">
          <div class="brand-name">HereAssistant</div>
          <div class="brand-caption">AI workspace</div>
        </div>
      </div>

      <div class="nav-caption">Рабочее пространство</div>
      <nav class="space-y-1">
        <NuxtLink to="/" class="nav-item" active-class="nav-item-active">
          <AppNavIcon name="now" />Сейчас
        </NuxtLink>
        <NuxtLink to="/history" class="nav-item" active-class="nav-item-active">
          <AppNavIcon name="history" />История
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
        <span>Self-hosted</span>
        <span class="ml-auto text-text-dim">privacy first</span>
      </div>
    </aside>

    <main class="app-main">
      <div class="app-content"><NuxtPage /></div>
    </main>

    <nav class="mobile-tabs">
      <NuxtLink to="/" class="tab-item" active-class="tab-item-active"><AppNavIcon name="now" /><span>Сейчас</span></NuxtLink>
      <NuxtLink to="/history" class="tab-item" active-class="tab-item-active"><AppNavIcon name="history" /><span>История</span></NuxtLink>
      <NuxtLink to="/edits" class="tab-item" active-class="tab-item-active"><AppNavIcon name="edits" /><span>Правки</span></NuxtLink>
      <NuxtLink to="/stats" class="tab-item" active-class="tab-item-active"><AppNavIcon name="stats" /><span>RTK</span></NuxtLink>
      <NuxtLink to="/settings" class="tab-item" active-class="tab-item-active"><AppNavIcon name="git" /><span>Git</span></NuxtLink>
    </nav>
  </div>
</template>
