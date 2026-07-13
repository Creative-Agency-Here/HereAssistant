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
      <path d="M8 18a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
      <path d="M8 12V5a3 3 0 0 1 3-3h3M16 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM16 12v7a3 3 0 0 1-3 3h-3" />
    </template>
  </svg>
</template>
