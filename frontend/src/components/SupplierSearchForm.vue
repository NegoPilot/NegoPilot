<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import type { Catalog } from '@/api/types'
import { parsePositiveInteger } from '@/utils/format'
import { type SearchFormValues, TEXT_MAX, emptySearchValues } from '@/utils/search'

const props = defineProps<{
  catalog: Catalog | null
  catalogError: boolean
  initial: SearchFormValues
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [values: SearchFormValues]
  reset: []
}>()

const form = reactive<SearchFormValues>(emptySearchValues())

// URL이 바뀌면(뒤로 가기, 초기화) 폼도 맞춘다
watch(
  () => props.initial,
  (values) => Object.assign(form, values),
  { immediate: true },
)

const capacityInvalid = computed(
  () => form.minMonthlyCapacity.trim() !== '' && parsePositiveInteger(form.minMonthlyCapacity) === null,
)
const canSubmit = computed(() => form.process.trim() !== '' && !capacityInvalid.value && !props.loading)

const processOptions = computed(() => props.catalog?.processes.map((p) => p.process) ?? [])

// 입력한 공정이 카탈로그에 있으면 그 공정의 소재만, 아니면 전체 소재를 제안한다
const materialOptions = computed(() => {
  const processes = props.catalog?.processes ?? []
  const matched = processes.find((p) => p.process === form.process.trim())
  const all = matched ? matched.materials : processes.flatMap((p) => p.materials)
  return [...new Set(all)]
})

// URL에 카탈로그에 없는 지역이 들어와도 선택값이 사라지지 않게 한다
const regionOptions = computed(() => {
  const regions = props.catalog?.regions ?? []
  return form.region && !regions.includes(form.region) ? [form.region, ...regions] : regions
})

function onSubmit() {
  if (!canSubmit.value) return
  emit('submit', { ...form })
}

function onReset() {
  Object.assign(form, emptySearchValues())
  emit('reset')
}
</script>

<template>
  <form class="panel search-form" aria-label="협력사 검색 조건" @submit.prevent="onSubmit">
    <div class="fields">
      <div class="field">
        <label for="f-process">공정 <span aria-hidden="true" class="required">*</span></label>
        <input
          id="f-process"
          v-model="form.process"
          type="text"
          list="f-process-options"
          required
          :maxlength="TEXT_MAX"
          placeholder="예: 다이캐스팅"
          autocomplete="off"
        />
        <datalist id="f-process-options">
          <option v-for="p in processOptions" :key="p" :value="p" />
        </datalist>
      </div>

      <div class="field">
        <label for="f-material">소재</label>
        <input
          id="f-material"
          v-model="form.material"
          type="text"
          list="f-material-options"
          :maxlength="TEXT_MAX"
          placeholder="예: ADC12"
          autocomplete="off"
        />
        <datalist id="f-material-options">
          <option v-for="m in materialOptions" :key="m" :value="m" />
        </datalist>
      </div>

      <div class="field">
        <label for="f-capacity">최소 월 생산능력 (EA)</label>
        <input
          id="f-capacity"
          v-model="form.minMonthlyCapacity"
          type="text"
          inputmode="numeric"
          placeholder="예: 50,000"
          autocomplete="off"
          :aria-invalid="capacityInvalid"
          :aria-describedby="capacityInvalid ? 'f-capacity-error' : undefined"
        />
        <span v-if="capacityInvalid" id="f-capacity-error" class="field-error">숫자만 입력해 주세요 (예: 50000)</span>
      </div>

      <div class="field">
        <label for="f-region">지역</label>
        <select id="f-region" v-model="form.region">
          <option value="">전체</option>
          <option v-for="r in regionOptions" :key="r" :value="r">{{ r }}</option>
        </select>
        <span v-if="catalogError" class="muted">지역 목록을 불러오지 못했습니다</span>
      </div>

      <div class="field checkbox-field">
        <span class="label">인증</span>
        <label class="checkbox">
          <input v-model="form.iatfRequired" type="checkbox" />
          IATF 16949 인증 보유
        </label>
      </div>
    </div>

    <div class="actions">
      <button class="btn" type="button" @click="onReset">초기화</button>
      <button class="btn btn-primary" type="submit" :disabled="!canSubmit">
        {{ loading ? '검색 중…' : '검색' }}
      </button>
    </div>
  </form>
</template>

<style scoped>
.search-form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 16px;
  padding: 16px;
}
.fields {
  flex: 1 1 640px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px 16px;
  align-items: start;
}
.actions {
  display: flex;
  gap: 8px;
}
.required {
  color: var(--danger);
}
.checkbox {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  font-size: 14px;
  color: var(--text);
  cursor: pointer;
}
.checkbox input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--primary);
}
</style>
