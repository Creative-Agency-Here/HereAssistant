<template>
  <div class="min-h-screen flex">
    <!-- сайдбар (на мобильном свернут в нижнюю плашку) -->
    <aside class="hidden md:flex md:w-56 flex-col border-r border-line bg-bg-soft p-4 gap-1">
      <div class="text-text-soft text-xs uppercase tracking-wider mb-3">HereAssistant</div>
      <NuxtLink to="/" class="nav-item" active-class="nav-item-active">Сейчас</NuxtLink>
      <NuxtLink to="/history" class="nav-item" active-class="nav-item-active">История</NuxtLink>
      <NuxtLink to="/edits" class="nav-item" active-class="nav-item-active">Правки</NuxtLink>
      <NuxtLink to="/stats" class="nav-item nav-item-disabled">Статистика</NuxtLink>
      <NuxtLink to="/settings" class="nav-item nav-item-disabled">Настройки</NuxtLink>
      <div class="mt-auto text-xs text-text-dim">
        v0.1.0
      </div>
    </aside>

    <main class="flex-1 p-4 md:p-8 max-w-5xl">
      <NuxtPage />
    </main>

    <!-- мобильный нижний таббар -->
    <nav class="md:hidden fixed bottom-0 inset-x-0 bg-bg-soft border-t border-line flex">
      <NuxtLink to="/" class="tab-item" active-class="tab-item-active">Сейчас</NuxtLink>
      <NuxtLink to="/history" class="tab-item" active-class="tab-item-active">История</NuxtLink>
      <NuxtLink to="/edits" class="tab-item" active-class="tab-item-active">Правки</NuxtLink>
    </nav>
  </div>
</template>

<style>
.nav-item {
  @apply px-3 py-2 rounded-lg text-text-soft hover:text-text hover:bg-bg-card transition text-sm;
}
.nav-item-active {
  @apply text-text bg-bg-card;
}
.nav-item-disabled {
  @apply opacity-40 pointer-events-none;
}
.tab-item {
  @apply flex-1 text-center py-3 text-text-soft text-sm;
}
.tab-item-active {
  @apply text-accent;
}
</style>
