<script setup lang="ts">
import { resolveSheetGesture } from '~/utils/activityEvents.mjs'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  tall?: boolean
  expanded?: boolean
}>(), { tall: false, expanded: false })

const emit = defineEmits<{ close: [] }>()
const expandedState = ref(false)
const dragOffset = ref(0)
let dragStart = 0
let dragging = false

const sheetStyle = computed(() => dragging ? { transform: `translateY(${dragOffset.value}px)`, transition: 'none' } : undefined)

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) emit('close')
}

watch(() => props.open, (open) => {
  document.documentElement.classList.toggle('sheet-open', open)
  if (open) expandedState.value = props.expanded
})
watch(() => props.expanded, (value) => {
  if (props.open && value) expandedState.value = true
})

function startDrag(event: PointerEvent) {
  if (!props.tall) return
  dragging = true
  dragStart = event.clientY
  dragOffset.value = 0
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}

function moveDrag(event: PointerEvent) {
  if (!dragging) return
  dragOffset.value = Math.max(-80, Math.min(220, event.clientY - dragStart))
}

function endDrag() {
  if (!dragging) return
  const delta = dragOffset.value
  dragging = false
  dragOffset.value = 0
  const action = resolveSheetGesture(delta, expandedState.value)
  if (action === 'expand') expandedState.value = true
  else if (action === 'close') emit('close')
  else if (action === 'collapse') expandedState.value = false
}

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
          :class="tall && expandedState ? 'activity-sheet-tall' : ''"
          :style="sheetStyle"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <button
            class="sheet-handle-button"
            type="button"
            aria-label="Потянуть для раскрытия"
            @pointerdown="startDrag"
            @pointermove="moveDrag"
            @pointerup="endDrag"
            @pointercancel="endDrag"
          ><span class="sheet-handle" /></button>
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
