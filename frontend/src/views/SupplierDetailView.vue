<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { createConversation } from '@/api/conversations'
import { getSupplier } from '@/api/suppliers'
import type { SupplierDetail } from '@/api/types'
import ErrorNotice from '@/components/ErrorNotice.vue'
import SupplierDetailPanel from '@/components/SupplierDetailPanel.vue'

const props = defineProps<{ supplierId: string }>()
const router = useRouter()

const supplier = ref<SupplierDetail | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)

const starting = ref(false)
const startError = ref<unknown>(null)

async function load() {
  loading.value = true
  error.value = null
  supplier.value = null
  try {
    supplier.value = await getSupplier(props.supplierId)
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

watch(() => props.supplierId, load, { immediate: true })

// 기업 하나를 문맥으로 새 대화를 만들고 대화 화면으로 간다
async function askAboutSupplier() {
  if (!supplier.value || starting.value) return
  starting.value = true
  startError.value = null
  try {
    const snapshot = await createConversation(supplier.value.supplier_id)
    await router.push({ name: 'chat', params: { conversationId: snapshot.conversation_id } })
  } catch (e) {
    startError.value = e
  } finally {
    starting.value = false
  }
}

function goBack() {
  // 검색 결과에서 왔으면 검색 조건을 그대로 두고 돌아간다
  if (window.history.state?.back) router.back()
  else router.push({ name: 'supplier-search' })
}
</script>

<template>
  <main class="page">
    <div>
      <button class="link-btn" type="button" @click="goBack">← 협력사 탐색</button>
    </div>

    <p v-if="loading" aria-live="polite">불러오는 중…</p>
    <section v-else-if="error" class="panel">
      <ErrorNotice :error="error" :retry="load" />
      <p class="panel-body"><RouterLink :to="{ name: 'supplier-search' }">협력사 탐색으로 돌아가기</RouterLink></p>
    </section>

    <template v-else-if="supplier">
      <header class="detail-head">
        <div>
          <span class="muted">{{ supplier.supplier_id }} · {{ supplier.region }}</span>
          <h1>{{ supplier.name }}</h1>
        </div>
        <button class="btn btn-primary" type="button" :disabled="starting" @click="askAboutSupplier">
          {{ starting ? '대화를 여는 중…' : 'AI에게 이 회사 질문하기' }}
        </button>
      </header>
      <section v-if="startError" class="panel"><ErrorNotice :error="startError" :retry="askAboutSupplier" /></section>

      <SupplierDetailPanel :supplier="supplier" />
    </template>
  </main>
</template>

<style scoped>
.link-btn {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  color: var(--primary);
  cursor: pointer;
}
.detail-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}
.detail-head h1 {
  font-size: 22px;
  margin-top: 2px;
}
</style>
