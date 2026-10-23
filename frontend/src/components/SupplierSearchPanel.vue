<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type { Catalog } from '@/api/types'
import { formatInteger } from '@/utils/format'
import { CAPACITY_MAX, type SearchFormValues, TEXT_MAX, emptySearchValues, parseCapacity } from '@/utils/search'

const props = defineProps<{
  catalog: Catalog | null
  catalogError: boolean
  /** URL에 반영된(=마지막으로 검색한) 조건 */
  applied: SearchFormValues
  rfq: string | null
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [values: SearchFormValues]
  reset: []
}>()

const form = reactive<SearchFormValues>(emptySearchValues())

// URL이 바뀌면(뒤로 가기, 초기화, 홈에서 RFQ 선택) 폼도 맞춘다
watch(
  () => props.applied,
  (values) => Object.assign(form, values),
  { immediate: true },
)

const capacityInvalid = computed(
  () => form.minMonthlyCapacity.trim() !== '' && parseCapacity(form.minMonthlyCapacity) === null,
)
const canSubmit = computed(() => !capacityInvalid.value && !props.loading)
const materialNotice = ref('')

function onProcessChange() {
  materialNotice.value = ''
  const process = props.catalog?.processes.find((p) => p.process === form.process.trim())
  if (process && form.material && !process.materials.includes(form.material.trim())) {
    form.material = ''
    materialNotice.value = '변경한 공정에 맞지 않는 소재를 해제했습니다.'
  }
}

const processOptions = computed(() => props.catalog?.processes.map((p) => p.process) ?? [])

// 입력한 공정이 카탈로그에 있으면 그 공정의 소재만, 아니면 전체 소재를 제안한다
const materialOptions = computed(() => {
  const processes = props.catalog?.processes ?? []
  const matched = processes.find((p) => p.process === form.process.trim())
  return [...new Set(matched ? matched.materials : processes.flatMap((p) => p.materials))]
})

// URL에 카탈로그에 없는 지역이 들어와도 선택값이 사라지지 않게 한다
const regionOptions = computed(() => {
  const regions = props.catalog?.regions ?? []
  return form.region && !regions.includes(form.region) ? [form.region, ...regions] : regions
})

const appliedChips = computed(() => {
  const chips: { label: string; primary: boolean }[] = []
  const a = props.applied
  if (a.q) chips.push({ label: `검색: ${a.q}`, primary: true })
  if (a.process) chips.push({ label: a.process, primary: true })
  if (a.material) chips.push({ label: a.material, primary: false })
  if (a.region) chips.push({ label: a.region, primary: false })
  if (a.iatfRequired) chips.push({ label: 'IATF 16949', primary: false })
  if (a.iso14001Required) chips.push({ label: 'ISO 14001', primary: false })
  const capacity = parseCapacity(a.minMonthlyCapacity)
  if (capacity !== null) chips.push({ label: `월 ${formatInteger(capacity)}개 이상`, primary: false })
  if (props.rfq) chips.push({ label: props.rfq, primary: false })
  return chips
})

function onSubmit() {
  if (canSubmit.value) emit('submit', { ...form, page: 0 })
}

function onReset() {
  materialNotice.value = ''
  Object.assign(form, emptySearchValues())
  emit('reset')
}
</script>

<template>
  <form class="panel search-panel" aria-labelledby="search-h" @submit.prevent="onSubmit">
    <div class="head">
      <h2 id="search-h">검색 조건</h2>
      <button class="link-btn" type="button" @click="onReset">초기화</button>
    </div>

    <p class="muted">조건 없이 전체 활성 협력사를 조회합니다. 원하는 조건만 선택하세요.</p>

    <div class="field">
      <label for="f-query">협력사명 또는 ID</label>
      <input id="f-query" v-model="form.q" type="search" :maxlength="TEXT_MAX" placeholder="이름 또는 SUP-0001" autocomplete="off" />
    </div>

    <div class="field">
      <label for="f-process">공정 (선택)</label>
      <input
        id="f-process"
        v-model="form.process"
        type="text"
        list="f-process-options"
        :maxlength="TEXT_MAX"
        placeholder="전체 공정"
        @change="onProcessChange"
        autocomplete="off"
      />
      <datalist id="f-process-options">
        <option v-for="p in processOptions" :key="p" :value="p" />
      </datalist>
    </div>

    <div class="field">
      <label for="f-material">소재 (선택)</label>
      <input
        id="f-material"
        v-model="form.material"
        type="text"
        list="f-material-options"
        :maxlength="TEXT_MAX"
        placeholder="전체 소재"
        autocomplete="off"
      />
      <p v-if="materialNotice" class="muted" role="status">{{ materialNotice }}</p>
      <datalist id="f-material-options">
        <option v-for="m in materialOptions" :key="m" :value="m" />
      </datalist>
    </div>

    <div class="field">
      <label for="f-region">지역</label>
      <select id="f-region" v-model="form.region">
        <option value="">전체</option>
        <option v-for="r in regionOptions" :key="r" :value="r">{{ r }}</option>
      </select>
      <span v-if="catalogError" class="muted">선택 목록을 불러오지 못했습니다. 공정과 소재를 직접 입력할 수 있습니다.</span>
    </div>

    <fieldset class="field certs">
      <legend class="label">인증</legend>
      <label class="checkbox">
        <input v-model="form.iatfRequired" type="checkbox" />
        IATF 16949 인증 보유
      </label>
      <label class="checkbox">
        <input v-model="form.iso14001Required" type="checkbox" />
        ISO 14001 인증 보유
      </label>
    </fieldset>

    <div class="field">
      <label for="f-capacity">최소 월 생산능력</label>
      <input
        id="f-capacity"
        v-model="form.minMonthlyCapacity"
        type="text"
        inputmode="numeric"
        placeholder="예: 10,000"
        autocomplete="off"
        :aria-invalid="capacityInvalid"
        :aria-describedby="capacityInvalid ? 'f-capacity-error' : undefined"
      />
      <span v-if="capacityInvalid" id="f-capacity-error" class="field-error">1~{{ formatInteger(CAPACITY_MAX) }} 사이 숫자만 입력해 주세요 (예: 10000)</span>
    </div>

    <button class="btn btn-primary submit" type="submit" :disabled="!canSubmit">
      {{ loading ? '검색 중…' : '검색하기' }}
    </button>

    <div class="applied">
      <h3>현재 검색 조건</h3>
      <div v-if="appliedChips.length" class="chips">
        <span v-for="c in appliedChips" :key="c.label" class="chip" :class="{ 'chip-primary': c.primary }">{{ c.label }}</span>
      </div>
      <p v-else class="muted">전체 활성 협력사</p>
      <p class="footnote">AI 대화는 협력사를 선택하지 않아도 바로 이용할 수 있습니다.</p>
    </div>
  </form>
</template>

<style scoped>
.search-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 18px 14px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.head h2 {
  font-size: 16px;
}
.certs {
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
.certs legend {
  padding: 0;
  margin-bottom: 6px;
}
.checkbox {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface);
  font-size: 13px;
  cursor: pointer;
}
.checkbox input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--primary);
}
.submit {
  width: 100%;
  margin-top: 8px;
}
.applied {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}
.applied h3 {
  font-size: 13px;
}
.applied p {
  margin: 0;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
@media (min-width: 641px) and (max-width: 1000px) {
  .search-panel { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
  .head, .search-panel > p, .applied { grid-column: 1 / -1; }
  .submit { align-self: end; }
}
</style>
