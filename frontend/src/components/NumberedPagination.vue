<script setup lang="ts">
import { computed } from 'vue'
import { formatInteger } from '@/utils/format'
import { pageNumbers } from '@/utils/search'

const props = defineProps<{ page: number; totalPages: number; total: number; loading?: boolean; label: string }>()
const emit = defineEmits<{ select: [page: number] }>()
const numbers = computed(() => pageNumbers(props.page, props.totalPages))
</script>

<template>
  <nav class="pagination" :aria-label="label">
    <p class="page-status" aria-live="polite">
      전체 <strong>{{ formatInteger(total) }}</strong>건
      <template v-if="totalPages > 0"> · {{ page + 1 }} / {{ formatInteger(totalPages) }}페이지</template>
      <template v-else> · 0페이지</template>
    </p>
    <div v-if="totalPages > 0" class="page-buttons">
      <button class="page-btn step" type="button" :disabled="loading || page <= 0" @click="emit('select', page - 1)">‹ 이전</button>
      <template v-for="(number, index) in numbers" :key="`${number}-${index}`">
        <span v-if="number === 'gap'" class="page-gap" aria-hidden="true">…</span>
        <button v-else class="page-btn" :class="{ current: number === page }" type="button"
          :aria-label="`${number + 1}페이지`" :aria-current="number === page ? 'page' : undefined"
          :disabled="loading" @click="emit('select', number)">{{ number + 1 }}</button>
      </template>
      <button class="page-btn step" type="button" :disabled="loading || page >= totalPages - 1" @click="emit('select', page + 1)">다음 ›</button>
    </div>
  </nav>
</template>

<style scoped>
/* 표와 같은 크기의 작은 글씨와 테두리 없는 버튼. 현재 페이지는 탭·배지처럼 연한 파랑으로 표시한다 */
.pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; }
.page-status { margin: 0; font-size: 12px; color: var(--text-3); }
.page-status strong { font-weight: 600; color: var(--text-2); }
.page-buttons { display: flex; flex-wrap: wrap; align-items: center; gap: 2px; }
.page-btn {
  min-width: 30px; height: 30px; padding: 0 8px;
  border: 0; border-radius: var(--radius); background: transparent;
  font: inherit; font-size: 12px; color: var(--text-2); cursor: pointer;
}
.page-btn:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
.page-btn.current { background: var(--primary-soft); color: var(--primary); font-weight: 700; }
.page-btn:disabled { color: var(--text-3); opacity: .5; cursor: not-allowed; }
.page-btn.current:disabled { opacity: 1; }
.step { padding: 0 10px; }
.page-gap { padding: 0 4px; font-size: 12px; color: var(--text-3); }
</style>
