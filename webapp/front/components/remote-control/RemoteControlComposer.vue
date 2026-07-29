<script setup lang="ts">
// Композер отправки промпта в живую /rc-сессию устройства (раздел «Ответ 9. UI»
// плана). Кнопка существует ТОЛЬКО при полном совпадении условий (capability
// бэка + состояние публикации + online устройства + privacy): неизвестная или
// выключенная capability не даёт disabled-кнопку, а прячет её целиком — вместо
// кнопки строка с точной причиной. Никакого v-html: текст промпта выводится как
// обычный текстовый узел.
import { computed, ref } from 'vue'
import type {
  RcComposerBlockReason,
  RcDeviceBadgeState,
  RemoteControlContext,
} from '~/composables/useRemoteControl'
import { RC_PROMPT_MAX_CHARS } from '~/composables/useRemoteControl'

const props = defineProps<{ rc: RemoteControlContext }>()

// Плейсхолдер объясняет, что произойдёт с сообщением ПРЯМО СЕЙЧАС: пока агент
// занят, промпт не отменяет текущий запуск, а встаёт в очередь устройства.
const PLACEHOLDER_BY_STATE: Partial<Record<RcDeviceBadgeState, string>> = {
  busy_local: 'Агент занят на устройстве — сообщение встанет в очередь…',
  busy_remote: 'Агент выполняет команду — сообщение встанет в очередь…',
  awaiting_approval: 'Устройство ждёт подтверждения — сообщение встанет в очередь…',
}
const placeholder = computed(
  () => PLACEHOLDER_BY_STATE[props.rc.badgeState.value] || 'Промпт для удалённой сессии /rc…',
)

const BLOCK_REASON_TEXT: Record<RcComposerBlockReason, string> = {
  no_publication: '',
  private_presence_only:
    'Приватный режим: доступно только присутствие. Разрешите CRM-приём промптов в настройках проекта, чтобы управлять сессией отсюда.',
  capability_unavailable: 'Устройство не разрешило приём удалённых промптов.',
  device_offline: 'Устройство офлайн — отправка недоступна.',
  publication_closed: 'Публикация закрыта — сессия больше не принимает команды.',
}
const blockReasonText = computed(() => {
  const reason = props.rc.promptBlockReason.value
  return reason ? BLOCK_REASON_TEXT[reason] : ''
})

const input = ref('')
async function submit() {
  const text = input.value
  if (!text.trim() || !props.rc.canSendPrompt.value) return
  input.value = ''
  const accepted = await props.rc.sendPrompt(text)
  // Отказ означает, что команда НЕ создана: возвращаем текст, чтобы человек
  // повторил отправку той же строкой (тот же ключ идемпотентности), а не набирал
  // заново — иначе повтор ушёл бы новым ключом и мог задвоить промпт.
  if (!accepted && !input.value.trim()) input.value = text
}
</script>

<template>
  <div v-if="rc.publication.value" data-rc-composer class="space-y-2">
    <div class="flex items-end gap-2">
      <textarea
        v-if="rc.canSendPrompt.value"
        v-model="input"
        rows="1"
        :maxlength="RC_PROMPT_MAX_CHARS"
        :placeholder="placeholder"
        class="flex-1 resize-y rounded-xl bg-bg-soft border border-line px-3 py-2 text-sm text-text
               placeholder:text-text-dim focus:border-accent/50 focus:outline-none"
        @keydown.enter.exact.prevent="submit"
      />
      <p v-else class="flex-1 text-xs text-warn py-2">{{ blockReasonText }}</p>

      <button
        v-if="rc.canSendPrompt.value"
        class="btn btn-primary shrink-0"
        :disabled="!input.trim() || rc.sendingPrompt.value"
        @click="submit"
      >
        {{ rc.sendingPrompt.value ? 'Отправка…' : 'Отправить' }}
      </button>
      <button
        v-if="rc.canStop.value"
        class="btn btn-danger shrink-0"
        :disabled="rc.sendingStop.value"
        @click="rc.sendStop"
      >
        {{ rc.sendingStop.value ? 'Останавливаю…' : 'Стоп' }}
      </button>
      <button
        v-if="rc.canClose.value"
        class="btn btn-danger shrink-0"
        :disabled="rc.closing.value"
        :title="'Снять публикацию (/rc off): очередь команд закроется, новые промпты и Git-действия станут недоступны до следующего /rc на устройстве'"
        @click="rc.closePublication"
      >
        {{ rc.closing.value ? 'Снимаю…' : '/rc off' }}
      </button>
    </div>
    <p v-if="rc.actionError.value" class="text-xs text-err">{{ rc.actionError.value }}</p>
  </div>
</template>
