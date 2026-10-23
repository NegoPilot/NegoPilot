<script setup lang="ts">
import type { ReviewRow } from '@/api/offer-proposals'

defineProps<{ rows: ReviewRow[] }>()
const safeUrl = (value: string | null) => value && /^https?:\/\//i.test(value) ? value : undefined
</script>

<template>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th class="n">#</th><th class="verdict">판정</th><th>검토 항목</th><th>검사 결과</th><th>법령 근거</th><th>조치</th></tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in rows" :key="row.id">
          <td class="n muted">{{ index + 1 }}</td>
          <td class="verdict" :class="row.verdict">{{ row.verdict === 'approved' ? '승인' : '반려' }}</td>
          <td class="item">{{ row.item }}</td>
          <td>{{ row.result }}<p v-if="row.reason" class="reason">반려 사유: {{ row.reason }}</p></td>
          <td class="muted">{{ row.basis }}</td>
          <td>
            <a v-if="safeUrl(row.action.url)" :href="safeUrl(row.action.url)" target="_blank" rel="noopener noreferrer">{{ row.action.label }}</a>
            <span v-else class="action">{{ row.action.label }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.n{width:36px}.verdict{width:80px;font-weight:600}.verdict.approved{color:var(--success)}.verdict.rejected{color:var(--danger)}
.item{font-weight:600}.reason{margin:4px 0 0;font-size:12px;color:var(--danger)}a,.action{font-size:13px;color:var(--primary);text-decoration:none}
</style>
