<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getCatalog, getSupplier, searchSuppliers } from '@/api/suppliers'
import type { Catalog, SupplierDetail, SupplierList, SupplierSort, SupplierSummary } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import ErrorNotice from '@/components/ErrorNotice.vue'
import NumberedPagination from '@/components/NumberedPagination.vue'
import SupplierDetailSection from '@/components/SupplierDetailSection.vue'
import SupplierSearchPanel from '@/components/SupplierSearchPanel.vue'
import { assistantOpen, openAssistant } from '@/stores/assistant'
import { NO_DATA, formatCapacity, formatInteger, formatKrw, formatPercent, formatPpm } from '@/utils/format'
import { provenanceLabel } from '@/utils/provenance'
import { PAGE_SIZES, SUPPLIER_SORTS, type SearchFormValues, safePriceSort, searchValuesFromQuery, searchValuesToParams, searchValuesToQuery } from '@/utils/search'

const route = useRoute()
const router = useRouter()
function queryString(key: string): string | null {
  const v = route.query[key]
  const s = Array.isArray(v) ? v[0] : v
  return typeof s === 'string' && s ? s : null
}
const catalog = ref<Catalog | null>(null)
const catalogError = ref(false)
const applied = computed(() => searchValuesFromQuery(route.query))
const rfq = computed(() => queryString('rfq'))
const searchKey = computed(() => JSON.stringify(searchValuesToQuery(applied.value)))
const result = ref<SupplierList | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
const invalidCapacity = ref(false)
const details = ref<Record<string, SupplierDetail | 'error'>>({})
const columnGroup = ref<'overview' | 'quality' | 'trade'>('overview')
const priceSortAvailable = computed(() => Boolean(applied.value.process.trim() && applied.value.material.trim()))
watch(() => applied.value.sort, (sort) => {
  if (['defect_ppm', 'on_time_delivery_rate', 'audit_grade'].includes(sort)) columnGroup.value = 'quality'
  else if (['years_dealing', 'payment_days', 'raw_material_ratio', 'latest_quote_date', 'latest_quote_price'].includes(sort)) columnGroup.value = 'trade'
  else if (['founded_year', 'monthly_capacity'].includes(sort)) columnGroup.value = 'overview'
}, { immediate: true })
let latest = 0

async function loadDetail(id: string) {
  if (details.value[id] && details.value[id] !== 'error') return
  try {
    const detail = await getSupplier(id)
    details.value = { ...details.value, [id]: detail }
  } catch {
    details.value = { ...details.value, [id]: 'error' }
  }
}

async function runSearch() {
  const id = ++latest
  const params = searchValuesToParams(applied.value)
  invalidCapacity.value = !params
  if (!params) {
    result.value = null
    error.value = null
    loading.value = false
    return
  }
  loading.value = true
  error.value = null
  try {
    const data = await searchSuppliers(params)
    if (id !== latest) return
    // 데이터 변경으로 마지막 페이지가 사라지면 존재하는 마지막 페이지로 이동한다.
    if (data.page > 0 && data.page >= data.total_pages) {
      await applyValues({ ...applied.value, page: Math.max(0, data.total_pages - 1) })
      return
    }
    result.value = data
  } catch (e) {
    if (id === latest) {
      error.value = e
      result.value = null
    }
  } finally {
    if (id === latest) loading.value = false
  }
}
watch(searchKey, runSearch, { immediate: true })
onMounted(async () => {
  try { catalog.value = await getCatalog() } catch { catalogError.value = true }
})

async function applyValues(next: SearchFormValues) {
  const query: Record<string, string> = searchValuesToQuery(safePriceSort(next))
  if (rfq.value) query.rfq = rfq.value
  if (selectedId.value) query.selected = selectedId.value
  if (JSON.stringify(searchValuesToQuery(next)) === searchKey.value) await runSearch()
  else await router.push({ query })
}
function onSubmit(next: SearchFormValues) { void applyValues({ ...next, page: 0 }) }
function onReset() { void router.push({ query: {} }) }
function movePage(page: number) { void applyValues({ ...applied.value, page }) }
function changeSort(event: Event) {
  void applyValues({ ...applied.value, sort: (event.target as HTMLSelectElement).value as SupplierSort, page: 0 })
}
function changeSize(event: Event) {
  void applyValues({ ...applied.value, size: Number((event.target as HTMLSelectElement).value), page: 0 })
}
function toggleDirection() { void applyValues({ ...applied.value, direction: applied.value.direction === 'asc' ? 'desc' : 'asc', page: 0 }) }
function sortColumn(sort: SupplierSort) {
  if (sort === 'latest_quote_price' && !priceSortAvailable.value) return
  void applyValues({ ...applied.value, sort, direction: applied.value.sort === sort && applied.value.direction === 'asc' ? 'desc' : 'asc', page: 0 })
}
function sortIndicator(sort: SupplierSort) { return applied.value.sort === sort ? (applied.value.direction === 'asc' ? ' ↑' : ' ↓') : '' }
function ariaSort(sort: SupplierSort) { return applied.value.sort === sort ? (applied.value.direction === 'asc' ? 'ascending' : 'descending') : 'none' }

const selectedId = computed(() => queryString('selected'))
const selectedDetail = computed(() => {
  const d = selectedId.value ? details.value[selectedId.value] : undefined
  return d && d !== 'error' ? d : null
})
const selectedError = computed(() => Boolean(selectedId.value && details.value[selectedId.value] === 'error'))
watch(selectedId, (id) => { if (id) void loadDetail(id) }, { immediate: true })
function select(s: SupplierSummary) { void router.replace({ query: { ...route.query, selected: s.supplier_id } }) }
const headerContext = computed(() => [rfq.value, applied.value.process, applied.value.material].filter(Boolean).join(' / '))
function maxCapacityPairs(supplier: SupplierSummary): string[] {
  return supplier.max_monthly_capacity == null ? [] : supplier.matched_capabilities
    .filter((capability) => capability.monthly_capacity === supplier.max_monthly_capacity)
    .map((capability) => `${capability.process} / ${capability.material}`)
}
function ratio(value: number | null | undefined) { return formatPercent(value == null ? null : value * 100) }
</script>

<template>
  <header class="page-header">
    <h1>협력사</h1>
    <span v-if="headerContext" class="context">{{ headerContext }}</span>
    <span class="right">전체 협력사 조회</span>
  </header>
  <main class="page-body">
    <div class="workspace">
      <SupplierSearchPanel :catalog="catalog" :catalog-error="catalogError" :applied="applied" :rfq="rfq" :loading="loading" @submit="onSubmit" @reset="onReset" />
      <div class="center">
        <section class="panel panel-pad" aria-labelledby="result-h" :aria-busy="loading">
          <div class="panel-title">
            <div>
              <h2 id="result-h">협력사 검색 결과</h2>
              <p class="sub" aria-live="polite"><template v-if="result">전체 {{ formatInteger(result.total) }}곳 / 현재 {{ result.returned_count }}곳</template><template v-else>전체 활성 협력사에서 조건에 맞는 결과를 조회합니다.</template></p>
            </div>
          </div>
          <div class="result-controls">
            <label>정렬 기준
              <select :value="applied.sort" :disabled="loading" @change="changeSort">
                <option v-for="sort in SUPPLIER_SORTS" :key="sort.value" :value="sort.value" :disabled="sort.value === 'latest_quote_price' && !priceSortAvailable">{{ sort.label }}</option>
              </select>
            </label>
            <button class="btn direction" type="button" :disabled="loading" @click="toggleDirection">{{ applied.direction === 'asc' ? '오름차순 ↑' : '내림차순 ↓' }}</button>
            <label>페이지당 표시
              <select :value="applied.size" :disabled="loading" @change="changeSize"><option v-for="size in PAGE_SIZES" :key="size" :value="size">{{ size }}곳</option></select>
            </label>
            <label>비교 항목
              <select v-model="columnGroup"><option value="overview">기본정보와 생산</option><option value="quality">품질과 납기</option><option value="trade">거래와 견적</option></select>
            </label>
          </div>
          <p class="footnote">전체 검색 결과를 정렬한 후 페이지를 나눕니다. 최근 견적 단가는 공정과 소재를 모두 지정해야 정렬할 수 있습니다.</p>
          <p v-if="loading" role="status">검색 중…</p>
          <ErrorNotice v-else-if="error" :error="error" :retry="runSearch" />
          <EmptyState v-else-if="invalidCapacity" title="생산능력 조건을 확인해 주세요">최소 월 생산능력에 올바른 숫자를 입력해 주세요.</EmptyState>
          <template v-else-if="result">
            <EmptyState v-if="!result.items.length" title="조건에 맞는 협력사가 없습니다">이름과 ID 또는 선택한 필터를 확인해 주세요. 초기화하면 전체 협력사를 볼 수 있습니다.</EmptyState>
            <div v-else class="table-wrap">
              <table>
                <caption class="sr-only">전체 {{ result.total }}곳 중 {{ result.page + 1 }}페이지의 협력사 비교</caption>
                <thead><tr>
                  <th :aria-sort="ariaSort('name')"><button type="button" @click="sortColumn('name')">협력사{{ sortIndicator('name') }}</button></th>
                  <th :aria-sort="ariaSort('region')"><button type="button" @click="sortColumn('region')">지역{{ sortIndicator('region') }}</button></th>
                  <template v-if="columnGroup === 'overview'">
                    <th :aria-sort="ariaSort('founded_year')"><button type="button" @click="sortColumn('founded_year')">설립연도{{ sortIndicator('founded_year') }}</button></th>
                    <th>공정 / 소재</th>
                    <th :aria-sort="ariaSort('monthly_capacity')"><button type="button" @click="sortColumn('monthly_capacity')">최대 월 생산능력{{ sortIndicator('monthly_capacity') }}</button></th>
                  </template>
                  <template v-else-if="columnGroup === 'quality'">
                    <th :aria-sort="ariaSort('defect_ppm')"><button type="button" @click="sortColumn('defect_ppm')">불량률{{ sortIndicator('defect_ppm') }}</button></th>
                    <th :aria-sort="ariaSort('on_time_delivery_rate')"><button type="button" @click="sortColumn('on_time_delivery_rate')">납기 준수율{{ sortIndicator('on_time_delivery_rate') }}</button></th>
                    <th :aria-sort="ariaSort('audit_grade')"><button type="button" @click="sortColumn('audit_grade')">품질감사등급{{ sortIndicator('audit_grade') }}</button></th>
                  </template>
                  <template v-else>
                    <th :aria-sort="ariaSort('years_dealing')"><button type="button" @click="sortColumn('years_dealing')">거래기간{{ sortIndicator('years_dealing') }}</button></th>
                    <th :aria-sort="ariaSort('payment_days')"><button type="button" @click="sortColumn('payment_days')">대금 지급기한{{ sortIndicator('payment_days') }}</button></th>
                    <th :aria-sort="ariaSort('raw_material_ratio')"><button type="button" @click="sortColumn('raw_material_ratio')">원재료 비중{{ sortIndicator('raw_material_ratio') }}</button></th>
                    <th :aria-sort="ariaSort('latest_quote_date')"><button type="button" @click="sortColumn('latest_quote_date')">최근 견적일{{ sortIndicator('latest_quote_date') }}</button></th>
                    <th :aria-sort="ariaSort('latest_quote_price')"><button type="button" :disabled="!priceSortAvailable" @click="sortColumn('latest_quote_price')">최근 단가{{ sortIndicator('latest_quote_price') }}</button></th>
                  </template>
                  <th>데이터 출처</th>
                </tr></thead>
                <tbody>
                  <tr v-for="s in result.items" :key="s.supplier_id" class="row" :class="{ selected: s.supplier_id === selectedId }" @click="select(s)">
                    <td><button class="row-btn" type="button" :aria-pressed="s.supplier_id === selectedId" @click.stop="select(s)">{{ s.name }}</button><div class="muted">{{ s.supplier_id }}</div></td>
                    <td>{{ s.region || NO_DATA }}</td>
                    <template v-if="columnGroup === 'overview'">
                      <td class="num">{{ s.founded_year ?? NO_DATA }}</td>
                      <td><template v-if="s.matched_capabilities.length"><div v-for="c in s.matched_capabilities" :key="`${c.process}-${c.material}`">{{ c.process }} / {{ c.material }}</div></template><template v-else>{{ NO_DATA }}</template></td>
                      <td class="num">{{ formatCapacity(s.max_monthly_capacity) }}<div v-for="pair in maxCapacityPairs(s)" :key="pair" class="muted">{{ pair }}</div></td>
                    </template>
                    <template v-else-if="columnGroup === 'quality'">
                      <td class="num">{{ formatPpm(s.quality.defect_ppm) }}</td>
                      <td class="num">{{ formatPercent(s.quality.on_time_delivery_rate) }}</td>
                      <td>{{ s.quality.audit_grade ?? NO_DATA }}</td>
                    </template>
                    <template v-else>
                      <td class="num">{{ s.quality.years_dealing == null ? NO_DATA : `${s.quality.years_dealing}년` }}</td>
                      <td class="num">{{ s.existing_contract?.payment_days == null ? NO_DATA : `${s.existing_contract.payment_days}일` }}</td>
                      <td class="num">{{ ratio(s.existing_contract?.raw_material_ratio) }}</td>
                      <td class="num">{{ s.latest_quote?.quoted_at ?? NO_DATA }}</td>
                      <td class="num">{{ formatKrw(s.latest_quote?.unit_price_krw) }}<div v-if="s.latest_quote" class="muted">{{ s.latest_quote.process }} / {{ s.latest_quote.material }}</div></td>
                    </template>
                    <td class="sources"><div>기업: {{ provenanceLabel(s.provenance.profile) }}</div><div>운영: {{ provenanceLabel(s.provenance.operations) }}</div></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <NumberedPagination :page="result.page" :total-pages="result.total_pages" :total="result.total" :loading="loading" label="협력사 검색 결과 페이지" @select="movePage" />
            <p class="footnote">생산능력은 조건에 맞는 공정과 소재 중 최대값입니다. 전체 합계가 아닙니다. 운영 지표가 가상 데이터인 경우 실제 조회 결과에도 해당 출처를 표시합니다.</p>
          </template>
        </section>
        <SupplierDetailSection v-if="selectedDetail" :supplier="selectedDetail" :process="applied.process" :material="applied.material" />
        <section v-else-if="selectedError" class="panel"><ErrorNotice :error="null" message="선택한 협력사 정보를 불러오지 못했습니다." :retry="() => selectedId && loadDetail(selectedId)" /></section>
        <p v-else-if="selectedId" role="status">협력사 상세를 불러오는 중…</p>
      </div>
    </div>
    <button v-if="!assistantOpen" class="btn btn-primary ai-fab" type="button" @click="openAssistant()">AI 대화로 이동</button>
  </main>
</template>

<style scoped>
.workspace { display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 18px; align-items: start; }
.center { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.center > .panel { min-width: 0; max-width: 100%; }
.table-wrap { max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain; }
.table-wrap table { min-width: 820px; }
.table-wrap td { white-space: nowrap; }
.table-wrap th:first-child, .table-wrap td:first-child { min-width: 180px; max-width: 260px; white-space: normal; word-break: keep-all; }
.table-wrap td:first-child .muted { white-space: nowrap; }
.table-wrap th:nth-child(2), .table-wrap td:nth-child(2) { min-width: 72px; }
.result-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
.result-controls label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.result-controls select { width: auto; min-width: 112px; }
.direction { margin-bottom: 1px; }
.row { cursor: pointer; }
.row:hover td, .row.selected td { background: var(--primary-soft); }
.row-btn, th button { border: 0; background: none; padding: 0; font: inherit; font-weight: 600; color: inherit; cursor: pointer; text-align: left; }
.row-btn:focus-visible, th button:focus-visible { outline: 2px solid var(--primary); outline-offset: 4px; }
.row.selected .row-btn { color: var(--primary); }
.sources { min-width: 150px; font-size: 12px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.ai-fab { position: fixed; right: 24px; bottom: 24px; min-width: 200px; height: 40px; box-shadow: 0 4px 14px rgba(30, 50, 90, 0.18); }
@media (max-width: 1000px) { .workspace { grid-template-columns: minmax(0, 1fr); } }
</style>
