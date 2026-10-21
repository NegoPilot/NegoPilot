<script setup lang="ts">
import type { StoredMessage } from '@/api/types'
import SupplierCard from './SupplierCard.vue'

defineProps<{
  message: StoredMessage
  conversationId: string
  /** 전송 중에는 선택지 버튼을 잠근다 */
  disabled: boolean
}>()

const emit = defineEmits<{ option: [text: string] }>()
</script>

<template>
  <li v-if="message.role === 'user'" class="msg msg-user">
    <span class="sr-only">내 질문:</span>
    {{ message.content }}
  </li>
  <li v-else class="msg msg-assistant">
    <div class="msg-meta">
      <span class="who">협상 도우미</span>
      <span class="muted">템플릿 응답</span>
    </div>
    <p class="msg-text">{{ message.response?.presentation.text ?? message.content }}</p>

    <div v-if="message.response?.presentation.cards.length" class="cards">
      <SupplierCard
        v-for="card in message.response.presentation.cards"
        :key="card.supplier.supplier_id"
        :card="card"
        :conversation-id="conversationId"
        :response-id="message.response.response_id"
      />
    </div>

    <div v-if="message.response?.clarification?.options.length" class="options" role="group" aria-label="이렇게 물어보세요">
      <button
        v-for="option in message.response.clarification.options"
        :key="option"
        class="chip"
        type="button"
        :disabled="disabled"
        @click="emit('option', option)"
      >
        {{ option }}
      </button>
    </div>
  </li>
</template>

<style scoped>
.msg {
  list-style: none;
}
.msg-user {
  align-self: flex-end;
  max-width: 78%;
  padding: 10px 14px;
  background: #eef0f2;
  border-radius: var(--radius);
  white-space: pre-line;
}
.msg-assistant {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.who {
  font-weight: 600;
  color: var(--text-2);
}
.msg-text {
  margin: 0;
  white-space: pre-line;
  line-height: 1.65;
}
.cards {
  display: grid;
  gap: 12px;
}
.options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.chip {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-strong);
  border-radius: 16px;
  background: var(--surface);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.chip:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}
.chip:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
