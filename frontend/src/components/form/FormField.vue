<script setup lang="ts">
// 라벨 + 입력칸(기본 슬롯) + 오류 또는 도움말. 오류가 있으면 도움말 대신 오류를 보여 준다
defineProps<{
  id: string
  label: string
  required?: boolean
  error?: string
  hint?: string
  /** 2: 두 칸 차지, full: 한 줄 전체 */
  span?: 2 | 'full'
}>()
</script>

<template>
  <div class="field" :class="{ span2: span === 2, full: span === 'full' }">
    <label :for="id">{{ label }}{{ required ? ' *' : '' }}</label>
    <slot />
    <span v-if="error" class="field-error">{{ error }}</span>
    <span v-else-if="hint || $slots.hint" class="hint"><slot name="hint">{{ hint }}</slot></span>
  </div>
</template>

<style scoped>
.field > label {
  font-weight: 600;
  color: var(--text);
}
.hint {
  font-size: 11px;
  color: var(--text-3);
}
.span2 {
  grid-column: span 2;
}
.full {
  grid-column: 1 / -1;
}
@media (max-width: 760px) {
  .span2 {
    grid-column: auto;
  }
}
</style>
