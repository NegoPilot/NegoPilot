<script setup lang="ts">
import { computed, nextTick, reactive, ref } from 'vue'

import { SOURCE_LABEL, addQuotes, createSupplier } from '@/api/admin-suppliers'
import FormField from '@/components/form/FormField.vue'
import FormGrid from '@/components/form/FormGrid.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import SavedNotice from '@/components/form/SavedNotice.vue'
import CapabilityRowsTable from '@/components/supplier-form/CapabilityRowsTable.vue'
import QualityFields from '@/components/supplier-form/QualityFields.vue'
import QuoteRowsTable from '@/components/supplier-form/QuoteRowsTable.vue'
import SupplierProfileFields from '@/components/supplier-form/SupplierProfileFields.vue'
import SupplierRegisterSummary from '@/components/supplier-form/SupplierRegisterSummary.vue'
import { describeError, isApiError } from '@/utils/errors'
import { todayIso } from '@/utils/format'
import {
  createRequest,
  emptyCapability,
  emptyQuote,
  emptyRegisterForm,
  quotesInput,
  validateRegister,
} from '@/utils/supplierForm'

const thisYear = new Date().getFullYear()
const today = todayIso()

const form = reactive(emptyRegisterForm())
const tried = ref(false)
const saving = ref(false)
const saveError = ref<string | null>(null)
/** 서버가 거절한 입력칸 (예: 이미 쓰는 DART 고유번호) */
const serverErrors = ref<Record<string, string>>({})
const done = ref<{ id: string; name: string; warning: string | null } | null>(null)

const localErrors = computed(() => validateRegister(form, thisYear, today))
const errors = computed(() => ({ ...localErrors.value, ...serverErrors.value }))
const errorCount = computed(() => Object.keys(errors.value).length)
function err(key: string): string | undefined {
  return tried.value ? errors.value[key] : undefined
}
const requestBody = computed(() => (Object.keys(localErrors.value).length ? null : createRequest(form)))

function clearForm() {
  Object.assign(form, emptyRegisterForm())
  tried.value = false
  saveError.value = null
  serverErrors.value = {}
}

async function submit() {
  if (saving.value) return
  tried.value = true
  done.value = null
  saveError.value = null
  serverErrors.value = {}
  if (errorCount.value) {
    // aria-invalid는 다음 렌더링에 붙으므로 오류 표시를 그린 뒤 첫 오류 칸을 찾는다
    await nextTick()
    document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
    return
  }
  saving.value = true
  try {
    const created = await createSupplier(createRequest(form))
    const { supplier_id: id, name } = created.supplier
    // 견적은 등록 API가 받지 않아서 등록 직후 견적 추가 API로 넣는다. 실패해도 등록은 이미 끝났다
    let warning: string | null = null
    if (form.quotes.length) {
      try {
        await addQuotes(id, created.supplier.version, quotesInput(form.quotes))
      } catch (error) {
        warning = `견적 ${form.quotes.length}건은 추가하지 못했습니다. (${describeError(error).message}) 상세 화면에서 다시 추가해 주세요.`
      }
    }
    done.value = { id, name, warning }
    clearForm()
    window.scrollTo({ top: 0 })
  } catch (error) {
    if (isApiError(error, 'DATA_CONFLICT')) serverErrors.value = { corpCode: '이미 다른 협력사가 쓰는 DART 고유번호입니다.' }
    saveError.value = describeError(error).message
  } finally {
    saving.value = false
  }
}

function reset() {
  if (!window.confirm('입력한 내용을 모두 지울까요?')) return
  clearForm()
}
</script>

<template>
  <main class="page-body">
    <p class="intro">
      협력사 DB에 새 협력사를 등록합니다. 협력사 ID는 서버가 정하고, 거래 조건과 재무는 관리자 화면에서 입력하지 않습니다.
    </p>
    <SavedNotice
      v-if="done"
      :message="`${done.name}(${done.id})를 등록했습니다.`"
      :to="{ name: 'admin-supplier-detail', params: { supplierId: done.id } }"
      link-text="상세 보기 →"
      :warning="done.warning"
    />

    <div class="layout">
      <form class="sections" novalidate @submit.prevent="submit">
        <PanelSection title="기본 정보" sub="DART 공시 기업은 등록 뒤 기본 정보를 수정할 수 없습니다. 인증은 수정할 수 있습니다.">
          <FormGrid :cols="3" class="source-row">
            <FormField id="r-source" label="기본 정보 출처" required>
              <select id="r-source" v-model="form.source">
                <option v-for="(label, key) in SOURCE_LABEL" :key="key" :value="key">{{ label }} ({{ key }})</option>
              </select>
            </FormField>
            <FormField v-if="form.source === 'DART'" id="r-corp" label="DART 고유번호" required :error="err('corpCode')">
              <input id="r-corp" v-model="form.corpCode" type="text" inputmode="numeric" maxlength="8" placeholder="8자리" :aria-invalid="!!err('corpCode')" />
            </FormField>
          </FormGrid>
          <SupplierProfileFields :profile="form.profile" :certs="form.certs" :err="err" />
        </PanelSection>

        <PanelSection title="생산능력" required sub="공정·소재 조합마다 한 줄. 협력사 검색은 이 표로 찾습니다.">
          <template #actions>
            <button class="btn" type="button" @click="form.capabilities.push(emptyCapability())">+ 공정 추가</button>
          </template>
          <CapabilityRowsTable :rows="form.capabilities" :err="err" />
        </PanelSection>

        <PanelSection title="품질·납기 실적" sub="선택 입력. 비워 두면 품질 없이 등록하고, 하나라도 채우면 네 칸 모두 필요합니다.">
          <QualityFields :quality="form.quality" :err="err" optional />
        </PanelSection>

        <PanelSection title="견적 이력" sub="선택 입력. 등록 직후 견적 추가로 넣습니다. 추천 순위는 생산능력과 같은 공정·소재의 견적만 씁니다.">
          <template #actions>
            <button class="btn" type="button" @click="form.quotes.push(emptyQuote())">+ 견적 추가</button>
          </template>
          <QuoteRowsTable v-if="form.quotes.length" :rows="form.quotes" :err="err" :today="today" />
          <p v-else class="muted">견적 이력이 없습니다.</p>
        </PanelSection>
      </form>

      <SupplierRegisterSummary
        :form="form"
        :request-body="requestBody"
        :tried="tried"
        :error-count="errorCount"
        :saving="saving"
        :save-error="saveError"
        @submit="submit"
        @reset="reset"
      />
    </div>
  </main>
</template>

<style scoped>
.intro {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 18px;
  align-items: start;
}
.sections {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
}
.source-row {
  margin-bottom: 14px;
}
@media (max-width: 1100px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
