<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import RequestSavedNotice from '@/components/form/RequestSavedNotice.vue'
import SupplierBasicSection from '@/components/supplier-register/SupplierBasicSection.vue'
import SupplierCapabilitySection from '@/components/supplier-register/SupplierCapabilitySection.vue'
import SupplierContractSection from '@/components/supplier-register/SupplierContractSection.vue'
import SupplierFinancialSection from '@/components/supplier-register/SupplierFinancialSection.vue'
import SupplierQualitySection from '@/components/supplier-register/SupplierQualitySection.vue'
import SupplierQuoteSection from '@/components/supplier-register/SupplierQuoteSection.vue'
import SupplierSavePreview from '@/components/supplier-register/SupplierSavePreview.vue'
import { SEED_SUPPLIER_MAX } from '@/data/dataCatalog'
import { useDataRequests } from '@/stores/dataRequests'
import { emptySupplierForm, supplierPayload, validateSupplier } from '@/utils/supplierForm'

const requests = useDataRequests()
const thisYear = new Date().getFullYear()

const takenIds = computed(() => {
  const ids = new Set<string>()
  for (let n = 1; n <= SEED_SUPPLIER_MAX; n++) ids.add(`SUP-${String(n).padStart(4, '0')}`)
  requests.state.items.filter((r) => r.kind === 'supplier').forEach((r) => ids.add(r.target))
  return ids
})
function nextId(): string {
  let n = SEED_SUPPLIER_MAX + 1
  while (takenIds.value.has(`SUP-${String(n).padStart(4, '0')}`)) n++
  return `SUP-${String(n).padStart(4, '0')}`
}

const form = reactive(emptySupplierForm(nextId()))
const tried = ref(false)
const done = ref<string | null>(null)

const errors = computed(() => validateSupplier(form, takenIds.value, thisYear))
const errorCount = computed(() => Object.keys(errors.value).length)
function err(key: string): string | undefined {
  return tried.value ? errors.value[key] : undefined
}
const payload = computed(() => supplierPayload(form))

function submit() {
  tried.value = true
  done.value = null
  if (errorCount.value) {
    document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
    return
  }
  const p = payload.value
  requests.add('supplier', p.suppliers.supplier_id, `${p.suppliers.name} · ${p.capabilities.map((c) => `${c.process} ${c.material}`).join(', ')}`, p)
  done.value = `${p.suppliers.name}(${p.suppliers.supplier_id}) 등록 요청을 저장했습니다.`
  Object.assign(form, emptySupplierForm(nextId()))
  tried.value = false
  window.scrollTo({ top: 0 })
}

function reset() {
  if (!window.confirm('입력한 내용을 모두 지울까요?')) return
  Object.assign(form, emptySupplierForm(nextId()))
  tried.value = false
}
</script>

<template>
  <main class="page-body">
    <p class="intro">
      business-mcp DB에 새 협력사를 넣을 정보를 입력합니다. 저장 형식은 표(schema.sql) 열 이름 그대로 만들어집니다.
    </p>
    <RequestSavedNotice v-if="done" :message="done" />

    <div class="layout">
      <form class="sections" novalidate @submit.prevent="submit">
        <SupplierBasicSection :form="form" :err="err" />
        <SupplierCapabilitySection :form="form" :err="err" />
        <SupplierQualitySection :form="form" :err="err" :on-time-rate="payload.quality.on_time_rate" />
        <SupplierContractSection :form="form" :err="err" />
        <SupplierQuoteSection :form="form" :err="err" />
        <SupplierFinancialSection :form="form" :err="err" />
      </form>

      <SupplierSavePreview :form="form" :payload="payload" :tried="tried" :error-count="errorCount" @submit="submit" @reset="reset" />
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
@media (max-width: 1100px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
