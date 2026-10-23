<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { getSupplierQuotes } from '@/api/suppliers'
import type { Capability, QuoteSort, SortDirection, SupplierDetail, SupplierQuoteList } from '@/api/types'
import ErrorNotice from '@/components/ErrorNotice.vue'
import NumberedPagination from '@/components/NumberedPagination.vue'
import {
  NO_DATA,
  formatHeld,
  formatInteger,
  formatKrw,
  formatKrwCompact,
  formatPercent,
  formatPpm,
} from '@/utils/format'
import { provenanceLabel } from '@/utils/provenance'
import { TEXT_MAX } from '@/utils/search'

const props = defineProps<{
  supplier: SupplierDetail
  /** 검색 조건의 공정·소재. 맞는 생산능력을 대표값으로 보여 준다 */
  process?: string
  material?: string
}>()

type TabKey = 'overview' | 'production' | 'financials' | 'trade'
const TABS: { key: TabKey; label: string }[] = [
  { key: 'overview', label: '기업 개요' },
  { key: 'production', label: '생산과 품질' },
  { key: 'financials', label: '재무' },
  { key: 'trade', label: '거래와 견적' },
]
const tab = ref<TabKey>('overview')
watch(
  () => props.supplier.supplier_id,
  () => { tab.value = 'overview'; quotes.value = null; quotePage.value = 0; quoteProcess.value = ''; quoteMaterial.value = ''; quoteProcessInput.value = ''; quoteMaterialInput.value = ''; quoteSort.value = 'quoted_at'; quoteDirection.value = 'desc'; ++quoteRequest },
)

const mainCapability = computed<Capability | null>(() => {
  const caps = props.supplier.capabilities
  const matched = caps.filter(
    (c) => (!props.process || c.process === props.process) && (!props.material || c.material === props.material),
  )
  const pool = matched.length ? matched : caps
  return pool.reduce<Capability | null>((best, c) => (!best || c.monthly_capacity > best.monthly_capacity ? c : best), null)
})

const latestQuote = computed(() => {
  const quotes = [...props.supplier.recent_quotes].sort((a, b) => b.quoted_at.localeCompare(a.quoted_at))
  return quotes[0] ?? null
})

const source = computed(() => provenanceLabel(props.supplier.provenance.profile))
const quotes = ref<SupplierQuoteList | null>(null)
const quoteLoading = ref(false)
const quoteError = ref<unknown>(null)
const quotePage = ref(0)
const quoteProcess = ref('')
const quoteMaterial = ref('')
const quoteProcessInput = ref('')
const quoteMaterialInput = ref('')
const quoteSort = ref<QuoteSort>('quoted_at')
const quoteDirection = ref<SortDirection>('desc')
// 과거 견적은 현재 보유 공정과 다를 수 있다. 제안 목록은 입력값을 제한하지 않는다.
const quoteFilterPairs = computed(() => [...props.supplier.capabilities, ...props.supplier.recent_quotes, ...(quotes.value?.items ?? [])])
const quoteProcessOptions = computed(() => [...new Set(quoteFilterPairs.value.map((c) => c.process))])
const quoteMaterialOptions = computed(() => [...new Set(quoteFilterPairs.value.filter((c) => !quoteProcessInput.value.trim() || c.process === quoteProcessInput.value.trim()).map((c) => c.material))])
const quotePriceSortAvailable = computed(() => Boolean(quoteProcess.value && quoteMaterial.value))
let quoteRequest = 0
function changeQuoteFilter() {
  const process = quoteProcessInput.value.trim()
  const material = quoteMaterialInput.value.trim()
  const unchanged = process === quoteProcess.value && material === quoteMaterial.value && quotePage.value === 0
  quoteProcess.value = process
  quoteMaterial.value = material
  if (!quotePriceSortAvailable.value) quoteSort.value = 'quoted_at'
  quotePage.value = 0
  if (unchanged) void loadQuotes()
}
async function loadQuotes() {
  if (tab.value !== 'trade') return
  const requestId = ++quoteRequest
  quoteLoading.value = true
  quoteError.value = null
  try {
    const page = await getSupplierQuotes(props.supplier.supplier_id, {
      page: quotePage.value, size: 20, sort: quoteSort.value, direction: quoteDirection.value,
      process: quoteProcess.value || undefined, material: quoteMaterial.value || undefined,
    })
    if (requestId !== quoteRequest) return
    if (page.page > 0 && page.page >= page.total_pages) { quotePage.value = Math.max(0, page.total_pages - 1); return }
    quotes.value = page
  } catch (error) {
    if (requestId === quoteRequest) { quoteError.value = error; quotes.value = null }
  } finally {
    if (requestId === quoteRequest) quoteLoading.value = false
  }
}
watch([tab, () => props.supplier.supplier_id, quotePage, quoteProcess, quoteMaterial, quoteSort, quoteDirection], loadQuotes)
function ratio(value: number | null | undefined) { return formatPercent(value == null ? null : value * 100) }
function yesNo(value: boolean | null | undefined) { return value == null ? NO_DATA : value ? '있음' : '없음' }
function timestamp(value: string | null) { return value ? new Date(value).toLocaleString('ko-KR') : NO_DATA }
</script>

<template>
  <section class="panel panel-pad" aria-labelledby="detail-h">
    <div class="panel-title">
      <div>
        <h2 id="detail-h">선택된 협력사 상세</h2>
        <p class="sub">기업 기본정보와 생산 및 거래 기록을 확인합니다.</p>
      </div>
    </div>

    <div class="identity">
      <div>
        <h3 class="name">{{ supplier.name }}</h3>
        <p class="muted">
          {{ supplier.region }}
          <template v-if="mainCapability"> / {{ mainCapability.process }} / {{ mainCapability.material }}</template>
        </p>
      </div>
      <span class="badge">{{ supplier.supplier_id }} / {{ source }}</span>
    </div>

    <div class="kpi-row">
      <div class="kpi">
        <div class="label">대표 공정의 월 생산능력</div>
        <div class="value">{{ mainCapability ? `${formatInteger(mainCapability.monthly_capacity)}개` : NO_DATA }}</div>
      </div>
      <div class="kpi">
        <div class="label">불량률</div>
        <div class="value">{{ formatPpm(supplier.quality.defect_ppm) }}</div>
      </div>
      <div class="kpi">
        <div class="label">납기 준수율</div>
        <div class="value">{{ formatPercent(supplier.quality.on_time_delivery_rate) }}</div>
      </div>
    </div>

    <p class="footnote">생산능력은 {{ mainCapability ? `${mainCapability.process} / ${mainCapability.material}` : '등록된 공정' }}의 월 기준 개수입니다. 기업 전체 합계가 아닙니다. 운영 정보 출처: {{ provenanceLabel(supplier.provenance.operations) }}</p>

    <div class="tabs" role="tablist" aria-label="협력사 상세">
      <button
        v-for="t in TABS"
        :key="t.key"
        role="tab"
        type="button"
        class="tab"
        :class="{ on: tab === t.key }"
        :aria-selected="tab === t.key"
        @click="tab = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <div class="tab-body">
      <div v-if="tab === 'overview'" class="boxes">
        <div class="info-box">
          <h3>기본정보</h3>
          <dl>
            <dt>협력사 ID</dt><dd>{{ supplier.supplier_id }}</dd>
            <dt>소재지</dt><dd>{{ supplier.region || NO_DATA }}</dd>
            <dt>설립연도</dt><dd>{{ supplier.founded_year ?? NO_DATA }}</dd>
            <dt>임직원 수</dt><dd>{{ supplier.employees == null ? NO_DATA : `${formatInteger(supplier.employees)}명` }}</dd>
            <dt>DART 기업 코드</dt><dd>{{ supplier.corp_code ?? NO_DATA }}</dd>
            <dt>종목 코드</dt><dd>{{ supplier.stock_code ?? NO_DATA }}</dd>
            <dt>업종 코드</dt><dd>{{ supplier.induty_code ?? NO_DATA }}</dd>
            <dt>사업자번호</dt><dd>{{ supplier.biz_no_masked ?? NO_DATA }}</dd>
            <dt>IATF 16949</dt><dd>{{ formatHeld(supplier.iatf16949) }}</dd>
            <dt>ISO 14001</dt><dd>{{ formatHeld(supplier.iso14001) }}</dd>
            <dt>기업정보 출처</dt><dd>{{ source }}</dd>
            <dt>운영정보 출처</dt><dd>{{ provenanceLabel(supplier.provenance.operations) }}</dd>
            <dt>DB 등록일</dt><dd>{{ timestamp(supplier.created_at) }}</dd>
            <dt>DB 수정일</dt><dd>{{ timestamp(supplier.updated_at) }}</dd>
          </dl>
        </div>
        <div class="info-box">
          <h3>거래 정보</h3>
          <dl>
            <dt>최근 견적</dt><dd>{{ latestQuote ? formatKrw(latestQuote.unit_price_krw) : NO_DATA }}</dd>
            <dt>월 공급능력</dt><dd>{{ mainCapability ? `${formatInteger(mainCapability.monthly_capacity)}개` : NO_DATA }}</dd>
            <dt>공정</dt><dd>{{ mainCapability?.process ?? NO_DATA }}</dd>
            <dt>적용 소재</dt><dd>{{ mainCapability?.material ?? NO_DATA }}</dd>
            <dt>거래기간</dt><dd>{{ supplier.quality.years_dealing == null ? NO_DATA : `${supplier.quality.years_dealing}년` }}</dd>
          </dl>
        </div>
      </div>

      <div v-else-if="tab === 'production'" class="boxes">
        <div class="info-box wide-box">
          <h3>생산능력</h3>
          <div v-if="supplier.capabilities.length" class="table-wrap">
            <table>
              <thead><tr><th>공정</th><th>소재</th><th>월 생산능력</th></tr></thead>
              <tbody>
                <tr v-for="c in supplier.capabilities" :key="`${c.process}-${c.material}`">
                  <td>{{ c.process }}</td><td>{{ c.material }}</td><td class="num">{{ formatInteger(c.monthly_capacity) }}개</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted">생산능력 정보 없음</p>
        </div>
        <div class="info-box">
          <h3>품질과 납기</h3>
          <dl>
            <dt>품질감사등급</dt><dd>{{ supplier.quality.audit_grade ?? NO_DATA }}</dd>
            <dt>불량률</dt><dd>{{ formatPpm(supplier.quality.defect_ppm) }}</dd>
            <dt>납기 준수율</dt><dd>{{ formatPercent(supplier.quality.on_time_delivery_rate) }}</dd>
            <dt>IATF 16949</dt><dd>{{ formatHeld(supplier.iatf16949) }}</dd>
            <dt>ISO 14001</dt><dd>{{ formatHeld(supplier.iso14001) }}</dd>
          </dl>
        </div>
      </div>

      <div v-else-if="tab === 'financials'" class="info-box">
        <h3>재무</h3>
        <div v-if="supplier.financials.length" class="table-wrap">
          <table>
            <thead><tr><th>연도</th><th>재무제표</th><th>매출</th><th>영업이익</th><th>순이익</th><th>자산</th><th>부채</th><th>자본</th><th>부채비율</th><th>출처</th></tr></thead>
            <tbody>
              <tr v-for="f in supplier.financials" :key="`${f.year}-${f.fs_div}`">
                <td>{{ f.year }}</td>
                <td>{{ f.fs_div === 'CFS' ? '연결' : f.fs_div === 'OFS' ? '별도' : f.fs_div ?? NO_DATA }}</td>
                <td class="num">{{ formatKrwCompact(f.revenue_krw) }}</td>
                <td class="num">{{ formatKrwCompact(f.operating_profit_krw) }}</td>
                <td class="num">{{ formatKrwCompact(f.net_income_krw) }}</td>
                <td class="num">{{ formatKrwCompact(f.total_assets_krw) }}</td>
                <td class="num">{{ formatKrwCompact(f.total_liabilities_krw) }}</td>
                <td class="num">{{ formatKrwCompact(f.total_equity_krw) }}</td>
                <td class="num">{{ formatPercent(f.debt_ratio) }}</td>
                <td>{{ provenanceLabel(f.source) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="muted">재무 정보 없음</p>
      </div>

      <div v-else class="boxes">
        <div class="info-box">
          <h3>기존 계약</h3>
          <dl v-if="supplier.existing_contract">
            <dt>대금 지급기한</dt><dd>{{ supplier.existing_contract.payment_days }}일</dd>
            <dt>연동 약정</dt><dd>{{ yesNo(supplier.existing_contract.price_linkage) }}</dd>
            <dt>원재료 비중</dt><dd>{{ ratio(supplier.existing_contract.raw_material_ratio) }}</dd>
            <dt>자동 갱신</dt><dd>{{ yesNo(supplier.existing_contract.auto_renewal) }}</dd>
          </dl>
          <p v-else class="muted">기존 거래 없음</p>
        </div>
        <div class="info-box wide-box quotes-box" :aria-busy="quoteLoading">
          <h3>전체 견적 이력</h3>
          <p class="footnote">운영 정보 출처: {{ provenanceLabel(supplier.provenance.operations) }}. 단가 비교는 동일 공정과 소재를 지정한 경우에만 지원합니다.</p>
          <form class="quote-controls" @submit.prevent="changeQuoteFilter">
            <label for="quote-process">견적 공정
              <input id="quote-process" v-model="quoteProcessInput" type="text" list="quote-process-options" :maxlength="TEXT_MAX" placeholder="전체 공정" autocomplete="off" />
              <datalist id="quote-process-options"><option v-for="p in quoteProcessOptions" :key="p" :value="p" /></datalist>
            </label>
            <label for="quote-material">견적 소재
              <input id="quote-material" v-model="quoteMaterialInput" type="text" list="quote-material-options" :maxlength="TEXT_MAX" placeholder="전체 소재" autocomplete="off" />
              <datalist id="quote-material-options"><option v-for="m in quoteMaterialOptions" :key="m" :value="m" /></datalist>
            </label>
            <button class="btn" type="submit" :disabled="quoteLoading">견적 조건 적용</button>
            <label>견적 정렬
              <select v-model="quoteSort" @change="quotePage = 0"><option value="quoted_at">견적일</option><option value="unit_price_krw" :disabled="!quotePriceSortAvailable">단가</option></select>
            </label>
            <label>견적 정렬 방향
              <select v-model="quoteDirection" @change="quotePage = 0"><option value="desc">내림차순</option><option value="asc">오름차순</option></select>
            </label>
          </form>
          <p class="footnote">현재 보유 공정에 없는 과거 견적도 정확한 공정명과 소재명을 입력해 찾을 수 있습니다. 적용 조건: {{ quoteProcess || '전체 공정' }} / {{ quoteMaterial || '전체 소재' }}</p>
          <p v-if="quoteLoading" role="status">견적을 불러오는 중…</p>
          <ErrorNotice v-else-if="quoteError" :error="quoteError" :retry="loadQuotes" />
          <template v-else-if="quotes">
            <div v-if="quotes.items.length" class="table-wrap">
              <table>
                <thead><tr><th>견적 ID</th><th>견적일</th><th>공정</th><th>소재</th><th>단가</th></tr></thead>
                <tbody><tr v-for="q in quotes.items" :key="q.quote_id"><td>{{ q.quote_id }}</td><td>{{ q.quoted_at }}</td><td>{{ q.process }}</td><td>{{ q.material }}</td><td class="num">{{ formatKrw(q.unit_price_krw) }}</td></tr></tbody>
              </table>
            </div>
            <p v-else class="muted">조건에 맞는 견적 이력이 없습니다.</p>
            <NumberedPagination :page="quotes.page" :total-pages="quotes.total_pages" :total="quotes.total" :loading="quoteLoading" label="협력사 견적 이력 페이지" @select="quotePage = $event" />
          </template>
          <p class="footnote">견적 수량과 납기 등 추가 조건 및 건별 거래 실적은 현재 데이터에 포함되지 않습니다.</p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.identity {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px 16px;
  margin-bottom: 16px;
}
.name {
  font-size: 22px;
}
.identity p {
  margin: 4px 0 0;
}
.tabs {
  margin-top: 16px;
}
.tab-body {
  padding-top: 14px;
}
.boxes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
  gap: 12px;
}
.info-box { min-width: 0; }
.table-wrap { max-width: 100%; overflow-x: auto; }
.table-wrap td { white-space: nowrap; }
.wide-box { grid-column: span 1; }
.quotes-box { grid-column: 1 / -1; min-width: 0; }
.quote-controls { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; margin: 12px 0; }
.quote-controls label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.quote-controls select, .quote-controls input { min-width: 110px; max-width: 180px; width: auto; }
.info-box table td,
.info-box table th {
  background: transparent;
}
</style>
