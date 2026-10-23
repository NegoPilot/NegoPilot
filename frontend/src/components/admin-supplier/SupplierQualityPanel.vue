<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { type AdminSupplierDetail, SOURCE_LABEL, replaceQuality } from '@/api/admin-suppliers'
import EditablePanel from '@/components/form/EditablePanel.vue'
import QualityFields from '@/components/supplier-form/QualityFields.vue'
import { useSupplierSave } from '@/composables/useSupplierSave'
import { formatPercent, formatPpm } from '@/utils/format'
import { type FieldErrors, qualityDraft, qualityInput, validateQuality } from '@/utils/supplierForm'

const props = defineProps<{ detail: AdminSupplierDetail }>()
const emit = defineEmits<{ saved: [detail: AdminSupplierDetail]; stale: [] }>()

const editing = ref(false)
const tried = ref(false)
const draft = reactive(qualityDraft(null))
const errors = computed(() => {
  const e: FieldErrors = {}
  validateQuality(draft, e)
  return e
})
function err(key: string) {
  return tried.value ? errors.value[key] : undefined
}
const { saving, error, save } = useSupplierSave((d) => emit('saved', d), () => emit('stale'))

function startEdit() {
  Object.assign(draft, qualityDraft(props.detail.quality))
  tried.value = false
  error.value = null
  editing.value = true
}

async function submit() {
  tried.value = true
  const quality = qualityInput(draft)
  if (Object.keys(errors.value).length || !quality) return
  const s = props.detail.supplier
  if (await save(() => replaceQuality(s.supplier_id, s.version, quality))) editing.value = false
}
</script>

<template>
  <EditablePanel
    title="품질·납기 실적"
    :sub="`출처 ${SOURCE_LABEL[detail.supplier.quality_source]} · 네 값 모두 필요합니다.`"
    :editing="editing"
    :saving="saving"
    :error="error"
    :edit-label="detail.quality ? '수정' : '품질 입력'"
    @edit="startEdit"
    @cancel="editing = false"
    @save="submit"
  >
    <dl v-if="detail.quality" class="fields">
      <div><dt>불량률</dt><dd>{{ formatPpm(detail.quality.defect_ppm) }}</dd></div>
      <div><dt>납기 준수율</dt><dd>{{ formatPercent(detail.quality.on_time_rate * 100) }}</dd></div>
      <div><dt>품질 감사 등급</dt><dd>{{ detail.quality.audit_grade }}</dd></div>
      <div><dt>거래 기간</dt><dd>{{ detail.quality.years_dealing ? `${detail.quality.years_dealing}년` : '신규 후보 (0년)' }}</dd></div>
    </dl>
    <p v-else class="muted">품질 실적이 없습니다. 추천 순위에서 "품질 또는 견적 자료 미등록"으로 제외됩니다.</p>
    <template #edit>
      <QualityFields :quality="draft" :err="err" />
    </template>
  </EditablePanel>
</template>

<style scoped>
.fields {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
}
.fields dt {
  font-size: 11px;
  color: var(--text-3);
}
.fields dd {
  margin: 2px 0 0;
  font-size: 13px;
}
@media (max-width: 760px) {
  .fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
