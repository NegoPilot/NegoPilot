<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { type AdminSupplierDetail, SOURCE_LABEL, patchSupplier } from '@/api/admin-suppliers'
import EditablePanel from '@/components/form/EditablePanel.vue'
import SupplierProfileFields from '@/components/supplier-form/SupplierProfileFields.vue'
import { useSupplierSave } from '@/composables/useSupplierSave'
import { NO_DATA, formatHeld, formatInteger } from '@/utils/format'
import { type FieldErrors, changedProfile, profileDraft, validateProfile } from '@/utils/supplierForm'

const props = defineProps<{ detail: AdminSupplierDetail }>()
const emit = defineEmits<{ saved: [detail: AdminSupplierDetail]; stale: [] }>()

const s = computed(() => props.detail.supplier)
// DART로 들어온 기업은 기본 정보를 바꿀 수 없다. 서버도 profile_origin_source로 막는다
const profileLocked = computed(() => s.value.profile_origin_source === 'DART')
const sub = computed(() => {
  if (profileLocked.value) return 'DART 공시 기업이라 기본 정보는 수정할 수 없고 인증만 수정할 수 있습니다.'
  if (s.value.source === 'MOCK') return '가상 기업의 기본 정보를 수정하면 출처가 관리자 입력으로 바뀝니다.'
  return undefined
})

const editing = ref(false)
const tried = ref(false)
const draft = reactive({ profile: profileDraft(s.value), certs: { iatf16949: false, iso14001: false } })
const thisYear = new Date().getFullYear()
const errors = computed(() => {
  const e: FieldErrors = {}
  if (!profileLocked.value) validateProfile(draft.profile, thisYear, e)
  return e
})
function err(key: string) {
  return tried.value ? errors.value[key] : undefined
}
const { saving, error, save } = useSupplierSave((d) => emit('saved', d), () => emit('stale'))

const fields = computed(() => [
  { label: '협력사명', value: s.value.name },
  { label: '지역', value: s.value.region },
  { label: '설립연도', value: String(s.value.founded_year) },
  { label: '사업자등록번호', value: s.value.biz_no_masked },
  { label: '직원 수', value: s.value.employees === null ? NO_DATA : `${formatInteger(s.value.employees)}명` },
  { label: '최근 매출', value: s.value.revenue_eok === null ? NO_DATA : `${formatInteger(s.value.revenue_eok)}억 원` },
  { label: '종목코드', value: s.value.stock_code ?? NO_DATA },
  { label: '업종 코드', value: s.value.induty_code ?? NO_DATA },
  { label: 'DART 고유번호', value: s.value.corp_code ?? NO_DATA },
  { label: '처음 들어온 출처', value: SOURCE_LABEL[s.value.profile_origin_source] },
  { label: 'IATF 16949', value: formatHeld(s.value.iatf16949) },
  { label: 'ISO 14001', value: formatHeld(s.value.iso14001) },
])

function startEdit() {
  Object.assign(draft.profile, profileDraft(s.value))
  Object.assign(draft.certs, { iatf16949: s.value.iatf16949, iso14001: s.value.iso14001 })
  tried.value = false
  error.value = null
  editing.value = true
}

async function submit() {
  tried.value = true
  if (Object.keys(errors.value).length) return
  const profile = profileLocked.value ? {} : changedProfile(draft.profile, s.value)
  const certifications: Record<string, boolean> = {}
  if (draft.certs.iatf16949 !== s.value.iatf16949) certifications.iatf16949 = draft.certs.iatf16949
  if (draft.certs.iso14001 !== s.value.iso14001) certifications.iso14001 = draft.certs.iso14001
  if (!Object.keys(profile).length && !Object.keys(certifications).length) {
    editing.value = false
    return
  }
  if (await save(() => patchSupplier(s.value.supplier_id, s.value.version, { profile, certifications }))) editing.value = false
}
</script>

<template>
  <EditablePanel
    title="기본 정보·인증"
    :sub="sub"
    :editing="editing"
    :saving="saving"
    :error="error"
    :edit-label="profileLocked ? '인증 수정' : '수정'"
    @edit="startEdit"
    @cancel="editing = false"
    @save="submit"
  >
    <dl class="fields">
      <div v-for="f in fields" :key="f.label">
        <dt>{{ f.label }}</dt>
        <dd>{{ f.value }}</dd>
      </div>
    </dl>
    <template #edit>
      <SupplierProfileFields :profile="draft.profile" :certs="draft.certs" :err="err" :profile-locked="profileLocked" />
    </template>
  </EditablePanel>
</template>

<style scoped>
.fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
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
