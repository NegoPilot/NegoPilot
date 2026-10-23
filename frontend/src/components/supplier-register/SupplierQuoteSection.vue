<script setup lang="ts">
import EditableRowTable from '@/components/form/EditableRowTable.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import { PROCESSES } from '@/data/dataCatalog'
import { type SupplierErrorOf, type SupplierForm, emptyQuote } from '@/utils/supplierForm'

defineProps<{ form: SupplierForm; err: SupplierErrorOf }>()
</script>

<template>
  <PanelSection title="견적 이력" sub="quotes 표 · 단가 비교 근거. 선택 입력">
    <template #actions>
      <button class="btn" type="button" @click="form.quotes.push(emptyQuote())">+ 견적 추가</button>
    </template>
    <EditableRowTable
      v-if="form.quotes.length"
      v-slot="{ row: q, index: i }"
      :headers="['공정', '소재', '개당 단가 (원)', '견적일']"
      :rows="form.quotes"
      @remove="(i) => form.quotes.splice(i, 1)"
    >
      <td>
        <select v-model="q.process" :aria-label="`${i + 1}번째 견적 공정`" :aria-invalid="!!err(`quote.${i}.process`)">
          <option value="">선택</option>
          <option v-for="p in PROCESSES" :key="p" :value="p">{{ p }}</option>
        </select>
      </td>
      <td><input v-model="q.material" type="text" :aria-label="`${i + 1}번째 견적 소재`" :aria-invalid="!!err(`quote.${i}.material`)" /></td>
      <td><input v-model="q.unitPrice" type="text" inputmode="numeric" :aria-label="`${i + 1}번째 견적 단가`" placeholder="예: 3,900" :aria-invalid="!!err(`quote.${i}.unitPrice`)" /></td>
      <td><input v-model="q.quotedAt" type="date" :aria-label="`${i + 1}번째 견적일`" :aria-invalid="!!err(`quote.${i}.quotedAt`)" /></td>
    </EditableRowTable>
    <p v-else class="muted">견적 이력이 없습니다.</p>
  </PanelSection>
</template>
