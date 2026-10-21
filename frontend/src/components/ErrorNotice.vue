<script setup lang="ts">
import { computed } from 'vue'

import { describeError } from '@/utils/errors'

const props = defineProps<{
  error: unknown
  /** 코드별 기본 문구 대신 보여 줄 문장 */
  message?: string | null
  retry?: () => void
}>()

const view = computed(() => describeError(props.error))
</script>

<template>
  <div class="error-notice" role="alert">
    <p>{{ message || view.message }}</p>
    <button v-if="retry && view.retryable" class="btn" type="button" @click="retry()">다시 시도</button>
  </div>
</template>

<style scoped>
.error-notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  padding: 14px 16px;
  color: var(--danger);
}
.error-notice p {
  margin: 0;
}
</style>
