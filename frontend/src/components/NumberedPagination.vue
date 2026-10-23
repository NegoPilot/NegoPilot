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
      전체 {{ formatInteger(total) }}건
      <template v-if="totalPages > 0"> / {{ page + 1 }} / {{ formatInteger(totalPages) }}페이지</template>
      <template v-else> / 0페이지</template>
    </p>
    <div v-if="totalPages > 0" class="page-buttons">
      <button class="btn" type="button" :disabled="loading || page <= 0" @click="emit('select', page - 1)">이전</button>
      <template v-for="(number, index) in numbers" :key="`${number}-${index}`">
        <span v-if="number === 'gap'" class="page-gap" aria-hidden="true">…</span>
        <button v-else class="btn page-number" :class="{ 'btn-primary': number === page }" type="button"
          :aria-label="`${number + 1}페이지`" :aria-current="number === page ? 'page' : undefined"
          :disabled="loading" @click="emit('select', number)">{{ number + 1 }}</button>
      </template>
      <button class="btn" type="button" :disabled="loading || page >= totalPages - 1" @click="emit('select', page + 1)">다음</button>
    </div>
  </nav>
</template>

<style scoped>
.pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; }
.page-status { margin: 0; font-size: 13px; color: var(--text-2); }
.page-buttons { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
.page-number { min-width: 36px; }
.page-gap { padding: 0 5px; }
</style>
