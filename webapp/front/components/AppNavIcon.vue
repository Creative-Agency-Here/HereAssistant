<script setup lang="ts">
defineProps<{ name: 'now' | 'history' | 'edits' | 'stats' | 'git' }>()
</script>

<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <template v-if="name === 'now'">
      <path d="M13 2 4.5 13.5h7L11 22l8.5-11.5h-7L13 2Z" />
    </template>
    <template v-else-if="name === 'history'">
      <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
      <path d="M3 3v5h5M12 7v5l3 2" />
    </template>
    <template v-else-if="name === 'edits'">
      <path d="M4 19.5V21h1.5L18.8 7.7l-2.5-2.5L3 18.5Z" />
      <path d="m14.8 6.7 2.5 2.5M13 21h8" />
    </template>
    <template v-else-if="name === 'stats'">
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </template>
    <template v-else>
      <g fill="currentColor" stroke="none">
        <rect x="5.4" y="2" width="3.5" height="3.5" rx="0.65" />
        <path d="M5.4 15.1c0-.36.3-.65.66-.65h2.5c.36 0 .65.29.65.65V22H6.06a.65.65 0 0 1-.65-.65V15.1Z" />
        <rect x="14.6" y="18.1" width="3.5" height="3.5" rx="0.65" />
        <path d="M14.6 2.65c0-.36.3-.65.66-.65h2.19c.36 0 .65.29.65.65v4.5c1.1.06 2 .19 2.53.28.31.06.53.32.53.63v2.15c0 .43-.4.75-.82.67-1.77-.35-5.55-.96-9.16.14-3.85 1.17-8.2.4-9.61.1A.62.62 0 0 1 1.1 10.5V8.3c0-.42.39-.74.8-.67 1.54.27 5.3.73 9.17-.12a22.4 22.4 0 0 1 3.53-.51V2.65Z" />
        <path d="M1.57 17.33a.62.62 0 0 1-.47-.61v-2.2c0-.41.39-.74.8-.67 1.54.27 5.3.73 9.17-.12 4.02-.89 8.17-.3 9.57-.06.3.05.52.31.52.63v2.15c0 .43-.4.75-.82.67-1.77-.35-5.55-.96-9.16.14-3.85 1.17-8.2.4-9.61.07Z" />
      </g>
    </template>
  </svg>
</template>
