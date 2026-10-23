<script setup lang="ts">
import { computed, ref } from 'vue'

import { type AdminSupplierDetail, SOURCE_LABEL, addQuotes } from '@/api/admin-suppliers'
import EditablePanel from '@/components/form/EditablePanel.vue'
import QuoteRowsTable from '@/components/supplier-form/QuoteRowsTable.vue'
import { useSupplierSave } from '@/composables/useSupplierSave'
import { formatKrw, todayIso } from '@/utils/format'
import { type FieldErrors, type QuoteRow, emptyQuote, quotesInput, validateQuotes } from '@/utils/supplierForm'

const props = defineProps<{ detail: AdminSupplierDetail }>()
const emit = defineEmits<{ saved: [detail: AdminSupplierDetail]; stale: [] }>()

const today = todayIso()
const editing = ref(false)
const tried = ref(false)
const rows = ref<QuoteRow[]>([])
const errors = computed(() => {
  const e: FieldErrors = {}
  if (!rows.value.length) e.quotes = '추가할 견적을 한 건 이상 넣어 주세요.'
  validateQuotes(rows.value, today, e)
  return e
})
function err(key: string) {
  return tried.value ? errors.value[key] : undefined
}
const { saving, error, save } = useSupplierSave((d) => emit('saved', d), () => emit('stale'))

// 추천 순위는 생산능력과 같은 공정·소재의 견적만 쓴다. 견적이 없는 조합을 알려 준다
const unquoted = computed(() => {
  const quoted = new Set(props.detail.quotes.map((q) => `${q.process}/${q.material}`))
  return props.detail.capabilities.filter((c) => !quoted.has(`${c.process}/${c.material}`)).map((c) => `${c.process} ${c.material}`)
})

function startEdit() {
  rows.value = [emptyQuote()]
  tried.value = false
  error.value = null
  editing.value = true
}

async function submit() {
  tried.value = true
  if (Object.keys(errors.value).length) return
  const s = props.detail.supplier
  if (await save(() => addQuotes(s.supplier_id, s.version, quotesInput(rows.value)))) editing.value = false
}
</script>

<template>
  <EditablePanel
    title="견적 이력"
    sub="견적은 이력이라 고치거나 지우지 않고 더하기만 합니다."
    :editing="editing"
    :saving="saving"
    :error="error"
    edit-label="+ 견적 추가"
    @edit="startEdit"
    @cancel="editing = false"
    @save="submit"
  >
    <p v-if="unquoted.length" class="warn">
      견적이 없는 공정·소재: {{ unquoted.join(', ') }}. 이 조합은 추천 순위에서 제외됩니다.
    </p>
    <div v-if="detail.quotes.length" class="table-wrap">
      <table>
        <thead><tr><th scope="col">견적일</th><th scope="col">공정</th><th scope="col">소재</th><th scope="col">개당 단가</th><th scope="col">출처</th></tr></thead>
        <tbody>
          <tr v-for="q in detail.quotes" :key="q.quote_id">
            <td class="num">{{ q.quoted_at }}</td>
            <td>{{ q.process }}</td>
            <td>{{ q.material }}</td>
            <td class="num">{{ formatKrw(q.unit_price) }}</td>
            <td><span class="badge" :class="q.source === 'MANUAL' ? '' : 'badge-gray'">{{ SOURCE_LABEL[q.source] }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="muted">견적 이력이 없습니다.</p>
    <template #edit>
      <QuoteRowsTable :rows="rows" :err="err" :today="today" />
      <button class="btn add" type="button" @click="rows.push(emptyQuote())">+ 줄 추가</button>
    </template>
  </EditablePanel>
</template>

<style scoped>
.warn {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--danger);
}
.add {
  margin-top: 10px;
}
</style>
