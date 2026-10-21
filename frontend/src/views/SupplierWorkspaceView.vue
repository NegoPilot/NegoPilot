<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getCatalog, getSupplier, searchSuppliers } from '@/api/suppliers'
import type { Catalog, SupplierDetail, SupplierList, SupplierSummary } from '@/api/types'
import AiAssistantPanel from '@/components/AiAssistantPanel.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorNotice from '@/components/ErrorNotice.vue'
import SupplierDetailSection from '@/components/SupplierDetailSection.vue'
import SupplierSearchPanel from '@/components/SupplierSearchPanel.vue'
import { NO_DATA, formatInteger, formatPpm } from '@/utils/format'
import { provenanceLabel } from '@/utils/provenance'
import {
  type SearchFormValues,
  searchValuesFromQuery,
  searchValuesToParams,
  searchValuesToQuery,
} from '@/utils/search'

const route = useRoute()
const router = useRouter()

function queryString(key: string): string | null {
  const v = route.query[key]
  const s = Array.isArray(v) ? v[0] : v
  return typeof s === 'string' && s ? s : null
}

// ── 검색 ──
const catalog = ref<Catalog | null>(null)
const catalogError = ref(false)
const applied = computed(() => searchValuesFromQuery(route.query))
const rfq = computed(() => queryString('rfq'))
const searchKey = computed(() => JSON.stringify(searchValuesToQuery(applied.value)))

const result = ref<SupplierList | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
// 결과 표의 불량률은 상세 조회로 채운다 (검색 결과 요약에는 품질 정보가 없다)
const details = ref<Record<string, SupplierDetail | 'error'>>({})
let latest = 0

async function loadDetail(id: string) {
  if (details.value[id] && details.value[id] !== 'error') return
  try {
    // 펼치기 전에 기다려야 동시에 끝난 다른 상세 결과를 덮어쓰지 않는다
    const detail = await getSupplier(id)
    details.value = { ...details.value, [id]: detail }
  } catch {
    details.value = { ...details.value, [id]: 'error' }
  }
}

async function runSearch() {
  const params = searchValuesToParams(applied.value)
  if (!params) {
    result.value = null
    error.value = null
    return
  }
  const id = ++latest
  loading.value = true
  error.value = null
  try {
    const data = await searchSuppliers(params)
    if (id !== latest) return
    result.value = data
    data.items.forEach((s) => loadDetail(s.supplier_id))
  } catch (e) {
    if (id === latest) {
      error.value = e
      result.value = null
    }
  } finally {
    if (id === latest) loading.value = false
  }
}

// 선택·AI 패널이 바뀔 때는 다시 검색하지 않는다
watch(searchKey, runSearch, { immediate: true })

onMounted(async () => {
  try {
    catalog.value = await getCatalog()
  } catch {
    catalogError.value = true
  }
})

function onSubmit(next: SearchFormValues) {
  const query: Record<string, string> = searchValuesToQuery(next)
  if (rfq.value) query.rfq = rfq.value
  if (aiOpen.value) query.ai = '1'
  if (JSON.stringify(searchValuesToQuery(next)) === searchKey.value) runSearch()
  else router.push({ query })
}

function onReset() {
  router.push({ query: aiOpen.value ? { ai: '1' } : {} })
}

// ── 선택 협력사 ──
const selectedId = computed(() => queryString('selected') ?? result.value?.items[0]?.supplier_id ?? null)
const selectedDetail = computed(() => {
  const d = selectedId.value ? details.value[selectedId.value] : undefined
  return d && d !== 'error' ? d : null
})
const selectedError = computed(() => (selectedId.value ? details.value[selectedId.value] === 'error' : false))

// 검색 결과 밖의 기업(예: 예전 상세 링크)이 선택되면 따로 불러온다
watch(selectedId, (id) => id && loadDetail(id), { immediate: true })

function select(s: SupplierSummary) {
  router.replace({ query: { ...route.query, selected: s.supplier_id } })
}

function capacityOf(s: SupplierSummary): string {
  const max = s.matched_capabilities.reduce((m, c) => Math.max(m, c.monthly_capacity), 0)
  return max ? formatInteger(max) : NO_DATA
}

function defectOf(id: string): string {
  const d = details.value[id]
  if (d === undefined) return '…'
  return d === 'error' ? NO_DATA : formatPpm(d.quality.defect_ppm)
}

// ── AI 패널 ──
const aiOpen = computed(() => queryString('ai') === '1')
function setAi(open: boolean) {
  const query = { ...route.query }
  if (open) query.ai = '1'
  else {
    delete query.ai
    delete query.q
  }
  router.replace({ query })
}
const backPath = computed(() => route.fullPath)

const headerContext = computed(() => {
  const parts: string[] = []
  if (rfq.value) parts.push(rfq.value)
  const pm = [applied.value.process, applied.value.material].filter(Boolean).join(' / ')
  if (pm) parts.push(pm)
  return parts.join(' · ')
})
</script>

<template>
  <header class="page-header">
    <h1>협력사</h1>
    <span v-if="headerContext" class="context">{{ headerContext }}</span>
    <span class="right"><span class="badge badge-gray">시연 데이터 · MOCK</span></span>
  </header>

  <main class="page-body">
    <div class="workspace" :class="{ 'ai-open': aiOpen }">
      <SupplierSearchPanel
        :catalog="catalog"
        :catalog-error="catalogError"
        :applied="applied"
        :rfq="rfq"
        :loading="loading"
        @submit="onSubmit"
        @reset="onReset"
      />

      <div class="center">
        <section class="panel panel-pad" aria-labelledby="result-h" :aria-busy="loading">
          <div class="panel-title">
            <div>
              <h2 id="result-h">협력사 검색 결과</h2>
              <p class="sub">
                <template v-if="result">{{ result.returned_count }}곳 · 룰 기반 필터 결과</template>
                <template v-else>조건을 입력하고 검색하세요</template>
              </p>
            </div>
            <span class="badge">MOCK 예시</span>
          </div>

          <p v-if="loading" aria-live="polite">검색 중…</p>
          <ErrorNotice v-else-if="error" :error="error" :retry="runSearch" />
          <EmptyState v-else-if="!result" title="공정을 입력하고 검색하세요">홈의 RFQ 목록에서 '상세'를 누르면 조건이 채워집니다.</EmptyState>
          <EmptyState v-else-if="!result.items.length" title="조건에 맞는 협력사가 없습니다">조건을 줄이거나 다른 공정으로 검색해 보세요.</EmptyState>
          <template v-else>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>협력사</th>
                    <th>지역</th>
                    <th>공정</th>
                    <th>월 생산능력</th>
                    <th>불량률</th>
                    <th>출처</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="s in result.items"
                    :key="s.supplier_id"
                    class="row"
                    :class="{ selected: s.supplier_id === selectedId }"
                    @click="select(s)"
                  >
                    <td>
                      <button class="row-btn" type="button" :aria-pressed="s.supplier_id === selectedId" @click.stop="select(s)">
                        {{ s.name }}
                      </button>
                    </td>
                    <td>{{ s.region }}</td>
                    <td>{{ [...new Set(s.matched_capabilities.map((c) => c.process))].join(', ') }}</td>
                    <td class="num">{{ capacityOf(s) }}</td>
                    <td class="num">{{ defectOf(s.supplier_id) }}</td>
                    <td>{{ provenanceLabel(s.provenance.operations) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="footnote table-note">
              선택 업체: {{ selectedDetail?.name ?? selectedId ?? '없음' }}
              <template v-if="result.returned_count === result.limit"> · 최대 {{ result.limit }}곳까지 보여 줍니다</template>
            </p>
          </template>
        </section>

        <SupplierDetailSection
          v-if="selectedDetail"
          :supplier="selectedDetail"
          :process="applied.process"
          :material="applied.material"
        />
        <section v-else-if="selectedError" class="panel">
          <ErrorNotice :error="null" message="선택한 협력사 정보를 불러오지 못했습니다." />
        </section>
      </div>

      <AiAssistantPanel
        v-if="aiOpen"
        :initial-question="queryString('q') ?? ''"
        :rfq="rfq"
        :back-path="backPath"
        @close="setAi(false)"
      />
    </div>

    <button v-if="!aiOpen" class="btn btn-primary ai-fab" type="button" @click="setAi(true)">
      <span aria-hidden="true">✦</span> AI 도우미 열기
    </button>
  </main>
</template>

<style scoped>
.workspace {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}
.workspace.ai-open {
  grid-template-columns: 190px minmax(0, 1fr) 320px;
}
.center {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
}
.row {
  cursor: pointer;
}
.row:hover td {
  background: var(--primary-soft);
}
.row.selected td {
  background: var(--primary-soft);
}
.row-btn {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: 600;
  color: var(--text);
  cursor: pointer;
  text-align: left;
}
.row.selected .row-btn {
  color: var(--primary);
}
.table-note {
  margin: 8px 0 0;
}
.ai-fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  min-width: 200px;
  height: 40px;
  box-shadow: 0 4px 14px rgba(30, 50, 90, 0.18);
}
@media (max-width: 1180px) {
  .workspace.ai-open {
    grid-template-columns: 200px minmax(0, 1fr);
  }
  .workspace.ai-open > :last-child {
    grid-column: 1 / -1;
  }
}
@media (max-width: 760px) {
  .workspace,
  .workspace.ai-open {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
