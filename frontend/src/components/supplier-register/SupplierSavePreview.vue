<script setup lang="ts">
import { computed, ref, useId } from 'vue'

import { downloadJson } from '@/stores/dataRequests'
import type { SupplierForm, SupplierPayload } from '@/utils/supplierForm'

const props = defineProps<{
  form: SupplierForm
  payload: SupplierPayload
  /** 저장을 한 번 눌렀는지. 누르기 전에는 오류 수를 알리지 않는다 */
  tried: boolean
  errorCount: number
}>()
defineEmits<{ submit: []; reset: [] }>()

const headingId = useId()
const showJson = ref(false)

const tableCounts = computed(() => [
  { table: 'suppliers', rows: 1 },
  { table: 'capabilities', rows: props.form.capabilities.length },
  { table: 'quality', rows: 1 },
  { table: 'contracts', rows: props.form.hasContract ? 1 : 0 },
  { table: 'quotes', rows: props.form.quotes.length },
  { table: 'financials', rows: props.form.financials.length },
])
</script>

<template>
  <aside class="panel panel-pad preview" :aria-labelledby="headingId">
    <h2 :id="headingId">저장 미리보기</h2>
    <p class="sub muted">{{ form.name.trim() || '(협력사명)' }} · {{ form.supplierId }}</p>
    <table class="counts">
      <tbody>
        <tr v-for="t in tableCounts" :key="t.table">
          <td><code>{{ t.table }}</code></td>
          <td class="num">{{ t.rows }}행</td>
        </tr>
      </tbody>
    </table>
    <p v-if="tried && errorCount" class="field-error" role="alert">고칠 항목 {{ errorCount }}개가 있습니다.</p>
    <p v-else-if="!errorCount" class="ok">필수 항목을 모두 채웠습니다.</p>

    <button class="btn btn-primary wide" type="button" @click="$emit('submit')">등록 요청 저장</button>
    <div class="row2">
      <button class="btn" type="button" @click="downloadJson(`${form.supplierId}.json`, payload)">JSON 내려받기</button>
      <button class="btn" type="button" @click="$emit('reset')">초기화</button>
    </div>
    <button class="link-btn" type="button" :aria-expanded="showJson" @click="showJson = !showJson">
      {{ showJson ? '저장 형식 닫기' : '저장 형식(JSON) 보기' }}
    </button>
    <pre v-if="showJson" class="json">{{ JSON.stringify(payload, null, 2) }}</pre>
    <p class="footnote">
      등록 API가 아직 없어 DB에 바로 저장되지 않습니다. 등록 요청은 이 화면에만 남고, 내려받은 JSON으로 seed나 DB에 반영합니다.
    </p>
  </aside>
</template>

<style scoped>
.preview {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.preview h2 {
  font-size: 16px;
}
.preview .sub {
  margin: -6px 0 0;
}
.counts td {
  height: 30px;
  padding: 4px 8px;
  font-size: 12px;
}
.counts code {
  font-size: 12px;
}
.ok {
  margin: 0;
  font-size: 12px;
  color: var(--success);
}
.field-error {
  margin: 0;
}
.wide {
  width: 100%;
}
.row2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.link-btn {
  align-self: flex-start;
}
.json {
  max-height: 320px;
  overflow: auto;
  margin: 0;
  padding: 10px;
  font-size: 11px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.footnote {
  margin: 0;
}
@media (max-width: 1100px) {
  .preview {
    position: static;
  }
}
</style>
