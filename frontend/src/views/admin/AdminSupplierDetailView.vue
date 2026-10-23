<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { type AdminSupplierDetail, SOURCE_LABEL, STATUS_LABEL, getSupplier, setSupplierStatus } from '@/api/admin-suppliers'
import SupplierAuditList from '@/components/admin-supplier/SupplierAuditList.vue'
import SupplierCapabilityPanel from '@/components/admin-supplier/SupplierCapabilityPanel.vue'
import SupplierProfilePanel from '@/components/admin-supplier/SupplierProfilePanel.vue'
import SupplierQualityPanel from '@/components/admin-supplier/SupplierQualityPanel.vue'
import SupplierQuotePanel from '@/components/admin-supplier/SupplierQuotePanel.vue'
import SupplierTradeRecords from '@/components/admin-supplier/SupplierTradeRecords.vue'
import { useSupplierSave } from '@/composables/useSupplierSave'
import { describeError, isApiError } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{ supplierId: string }>()

const detail = ref<AdminSupplierDetail | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const notFound = ref(false)
const message = ref<string | null>(null)

let controller: AbortController | null = null
async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  loadError.value = null
  notFound.value = false
  try {
    detail.value = await getSupplier(props.supplierId, controller.signal)
  } catch (error) {
    if (isApiError(error, 'REQUEST_CANCELLED')) return
    detail.value = null
    notFound.value = isApiError(error, 'SUPPLIER_NOT_FOUND') || isApiError(error, 'VALIDATION_ERROR')
    loadError.value = describeError(error).message
  } finally {
    loading.value = false
  }
}
watch(() => props.supplierId, () => {
  message.value = null
  void load()
}, { immediate: true })
onUnmounted(() => controller?.abort())

function onSaved(next: AdminSupplierDetail) {
  detail.value = next
  message.value = `저장했습니다. (버전 ${next.supplier.version})`
}
/** 버전 충돌: 입력은 각 패널에 남겨 두고 최신 상세만 다시 받는다 */
function onStale() {
  message.value = null
  void load()
}

const status = useSupplierSave(onSaved, onStale)
async function toggleStatus() {
  const s = detail.value?.supplier
  if (!s) return
  const next = s.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE'
  const question = next === 'INACTIVE'
    ? `${s.name}를 비활성화할까요? 협력사 검색·추천 순위·AI 답변에서 빠집니다. 자료는 지우지 않고 다시 활성화할 수 있습니다.`
    : `${s.name}를 다시 활성화할까요?`
  if (!window.confirm(question)) return
  await status.save(() => setSupplierStatus(s.supplier_id, s.version, next))
}
</script>

<template>
  <main class="page-body">
    <RouterLink class="back" :to="{ name: 'admin-suppliers' }">← 협력사 목록</RouterLink>

    <p v-if="loading && !detail" class="muted" role="status">협력사 정보를 불러오는 중입니다.</p>
    <section v-else-if="loadError && !detail" class="panel panel-pad error-box" role="alert">
      <p>{{ notFound ? `${supplierId} 협력사를 찾을 수 없습니다.` : loadError }}</p>
      <button v-if="!notFound" class="btn" type="button" @click="load">다시 시도</button>
    </section>

    <template v-else-if="detail">
      <header class="panel panel-pad head">
        <div>
          <h2>{{ detail.supplier.name }} <code>{{ detail.supplier.supplier_id }}</code></h2>
          <p class="meta">
            <span class="badge" :class="detail.supplier.source === 'MANUAL' ? '' : 'badge-gray'">{{ SOURCE_LABEL[detail.supplier.source] }}</span>
            <span class="badge" :class="detail.supplier.status === 'ACTIVE' ? 'badge-gray' : 'badge-warning'">{{ STATUS_LABEL[detail.supplier.status] }}</span>
            <span class="muted">{{ detail.supplier.region }} · 버전 {{ detail.supplier.version }} · 수정 {{ formatDateTime(detail.supplier.updated_at) }}</span>
          </p>
        </div>
        <div class="head-actions">
          <button class="btn" type="button" :disabled="status.saving.value" @click="toggleStatus">
            {{ status.saving.value ? '변경 중…' : detail.supplier.status === 'ACTIVE' ? '비활성화' : '다시 활성화' }}
          </button>
        </div>
      </header>
      <p v-if="detail.supplier.status === 'INACTIVE'" class="inactive-note">비활성 협력사입니다. 협력사 검색·추천 순위·협상안 검사에서 빠져 있습니다.</p>
      <p v-if="status.error.value" class="field-error" role="alert">{{ status.error.value }}</p>
      <p v-if="message" class="message" role="status">{{ message }}</p>
      <p v-if="loadError" class="field-error" role="alert">최신 내용을 불러오지 못했습니다. {{ loadError }}</p>

      <SupplierProfilePanel :detail="detail" @saved="onSaved" @stale="onStale" />
      <SupplierCapabilityPanel :detail="detail" @saved="onSaved" @stale="onStale" />
      <SupplierQualityPanel :detail="detail" @saved="onSaved" @stale="onStale" />
      <SupplierQuotePanel :detail="detail" @saved="onSaved" @stale="onStale" />
      <SupplierTradeRecords :detail="detail" />
      <SupplierAuditList :audit="detail.audit" />
    </template>
  </main>
</template>

<style scoped>
.back {
  align-self: flex-start;
  font-size: 12px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.head h2 {
  margin: 0;
  font-size: 18px;
}
.head h2 code {
  margin-left: 6px;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-3);
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  font-size: 12px;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.inactive-note,
.message {
  margin: 0;
  padding: 10px 14px;
  font-size: 12px;
  border-radius: var(--radius);
}
.inactive-note {
  background: var(--surface-2);
  color: var(--text-2);
}
.message {
  background: var(--primary-soft);
  color: var(--primary);
}
.field-error {
  margin: 0;
}
.error-box p {
  margin: 0 0 10px;
  color: var(--danger);
}
</style>
