<script setup lang="ts">
const props = withDefaults(defineProps<{
  open: boolean
  title: string
  tall?: boolean
}>(), { tall: false })

const emit = defineEmits<{ close: [] }>()

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) emit('close')
}

watch(() => props.open, (open) => {
  document.documentElement.classList.toggle('sheet-open', open)
})

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  document.documentElement.classList.remove('sheet-open')
})
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet-fade">
      <div v-if="open" class="sheet-backdrop" @click.self="emit('close')">
        <section
          class="activity-sheet"
          :class="tall ? 'activity-sheet-tall' : ''"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <div class="sheet-handle" />
          <header class="sheet-header">
            <button class="sheet-close" type="button" aria-label="Закрыть" @click="emit('close')">
              <span aria-hidden="true">×</span>
            </button>
            <h2>{{ title }}</h2>
            <div class="size-12" />
          </header>
          <div class="sheet-content"><slot /></div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
