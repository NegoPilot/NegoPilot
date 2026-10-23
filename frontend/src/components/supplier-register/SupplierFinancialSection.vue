<script setup lang="ts">
import EditableRowTable from '@/components/form/EditableRowTable.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import { type FinancialRow, type SupplierErrorOf, type SupplierForm, emptyFinancial } from '@/utils/supplierForm'

defineProps<{ form: SupplierForm; err: SupplierErrorOf }>()

const AMOUNT_COLUMNS: { key: Exclude<keyof FinancialRow, 'year' | 'fsDiv'>; label: string }[] = [
  { key: 'revenue', label: '매출액' },
  { key: 'operatingIncome', label: '영업이익' },
  { key: 'netIncome', label: '당기순이익' },
  { key: 'totalAssets', label: '자산총계' },
  { key: 'totalLiabilities', label: '부채총계' },
  { key: 'totalEquity', label: '자본총계' },
]
const headers = ['사업연도', '구분', ...AMOUNT_COLUMNS.map((c) => c.label)]
</script>

<template>
  <PanelSection title="연도별 재무" sub="financials 표 · 단위 원. DART 기업은 단일회사 주요계정 값. 선택 입력">
    <template #actions>
      <button class="btn" type="button" @click="form.financials.push(emptyFinancial())">+ 연도 추가</button>
    </template>
    <EditableRowTable
      v-if="form.financials.length"
      v-slot="{ row: f, index: i }"
      :headers="headers"
      :rows="form.financials"
      @remove="(i) => form.financials.splice(i, 1)"
    >
      <td><input v-model="f.year" type="text" inputmode="numeric" maxlength="4" :aria-label="`${i + 1}번째 사업연도`" placeholder="2025" :aria-invalid="!!err(`fin.${i}.year`)" /></td>
      <td>
        <select v-model="f.fsDiv" :aria-label="`${i + 1}번째 재무제표 구분`">
          <option value="OFS">별도</option>
          <option value="CFS">연결</option>
        </select>
      </td>
      <td v-for="c in AMOUNT_COLUMNS" :key="c.key">
        <input v-model="f[c.key]" class="amount" type="text" inputmode="numeric" :aria-label="`${i + 1}번째 ${c.label}`" :aria-invalid="!!err(`fin.${i}.${c.key}`)" />
      </td>
    </EditableRowTable>
    <p v-else class="muted">재무 자료가 없으면 협력사 상세의 재무 탭은 '정보 없음'으로 보입니다.</p>
  </PanelSection>
</template>

<style scoped>
.amount {
  min-width: 96px;
}
</style>
