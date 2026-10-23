<script setup lang="ts">
import EditableRowTable from '@/components/form/EditableRowTable.vue'
import { PROCESSES } from '@/data/dataCatalog'
import type { FieldErrorOf, QuoteRow } from '@/utils/supplierForm'

// 새로 넣을 견적 입력 표. 견적일은 오늘까지만 고를 수 있다
defineProps<{ rows: QuoteRow[]; err: FieldErrorOf; today: string }>()
</script>

<template>
  <p v-if="err('quotes')" class="field-error">{{ err('quotes') }}</p>
  <EditableRowTable
    v-slot="{ row: q, index: i }"
    :headers="['공정', '소재', '개당 단가 (원)', '견적일']"
    :rows="rows"
    @remove="(i) => rows.splice(i, 1)"
  >
    <td>
      <select v-model="q.process" :aria-label="`${i + 1}번째 견적 공정`" :aria-invalid="!!err(`quote.${i}.process`)">
        <option value="">선택</option>
        <option v-for="p in PROCESSES" :key="p" :value="p">{{ p }}</option>
      </select>
    </td>
    <td><input v-model="q.material" type="text" maxlength="100" :aria-label="`${i + 1}번째 견적 소재`" :aria-invalid="!!err(`quote.${i}.material`)" /></td>
    <td>
      <input v-model="q.unitPrice" type="text" inputmode="numeric" :aria-label="`${i + 1}번째 견적 단가`" placeholder="예: 3,900" :aria-invalid="!!err(`quote.${i}.unitPrice`)" />
    </td>
    <td>
      <input v-model="q.quotedAt" type="date" :max="today" :aria-label="`${i + 1}번째 견적일`" :aria-invalid="!!err(`quote.${i}.quotedAt`)" />
      <span v-if="err(`quote.${i}.quotedAt`)" class="field-error">{{ err(`quote.${i}.quotedAt`) }}</span>
    </td>
  </EditableRowTable>
</template>
