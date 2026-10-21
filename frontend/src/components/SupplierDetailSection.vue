<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { Capability, SupplierDetail } from '@/api/types'
import {
  NO_DATA,
  formatInteger,
  formatKrw,
  formatKrwCompact,
  formatPercent,
  formatPpm,
} from '@/utils/format'
import { provenanceLabel } from '@/utils/provenance'

const props = defineProps<{
  supplier: SupplierDetail
  /** 검색 조건의 공정·소재. 맞는 생산능력을 대표값으로 보여 준다 */
  process?: string
  material?: string
}>()

type TabKey = 'overview' | 'production' | 'financials' | 'trade'
const TABS: { key: TabKey; label: string }[] = [
  { key: 'overview', label: '기업 개요' },
  { key: 'production', label: '생산·품질' },
  { key: 'financials', label: '재무' },
  { key: 'trade', label: '거래·견적' },
]
const tab = ref<TabKey>('overview')
watch(
  () => props.supplier.supplier_id,
  () => (tab.value = 'overview'),
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
</script>

<template>
  <section class="panel panel-pad" aria-labelledby="detail-h">
    <div class="panel-title">
      <div>
        <h2 id="detail-h">선택된 협력사 상세</h2>
        <p class="sub">business-mcp · get_supplier(supplier)</p>
      </div>
    </div>

    <div class="identity">
      <div>
        <h3 class="name">{{ supplier.name }}</h3>
        <p class="muted">
          {{ supplier.region }}
          <template v-if="mainCapability"> · {{ mainCapability.process }} / {{ mainCapability.material }}</template>
        </p>
      </div>
      <span class="badge">{{ supplier.supplier_id }} · {{ source }}</span>
    </div>

    <div class="kpi-row">
      <div class="kpi">
        <div class="label">월 생산능력</div>
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
            <dt>소재지</dt><dd>{{ supplier.region }}</dd>
            <dt>인증</dt><dd>{{ supplier.iatf16949 ? 'IATF 16949' : '인증 없음' }}</dd>
            <dt>출처</dt><dd>{{ source }}</dd>
          </dl>
        </div>
        <div class="info-box">
          <h3>거래 정보</h3>
          <dl>
            <dt>최근 견적</dt><dd>{{ latestQuote ? formatKrw(latestQuote.unit_price_krw) : NO_DATA }}</dd>
            <dt>월 공급능력</dt><dd>{{ mainCapability ? `${formatInteger(mainCapability.monthly_capacity)}개` : NO_DATA }}</dd>
            <dt>공정</dt><dd>{{ mainCapability?.process ?? NO_DATA }}</dd>
            <dt>적용 소재</dt><dd>{{ mainCapability?.material ?? NO_DATA }}</dd>
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
          <h3>품질·납기</h3>
          <dl>
            <dt>불량률</dt><dd>{{ formatPpm(supplier.quality.defect_ppm) }}</dd>
            <dt>납기 준수율</dt><dd>{{ formatPercent(supplier.quality.on_time_delivery_rate) }}</dd>
            <dt>인증</dt><dd>{{ supplier.iatf16949 ? 'IATF 16949' : '인증 없음' }}</dd>
          </dl>
        </div>
      </div>

      <div v-else-if="tab === 'financials'" class="info-box">
        <h3>재무</h3>
        <div v-if="supplier.financials.length" class="table-wrap">
          <table>
            <thead><tr><th>연도</th><th>매출</th><th>영업이익</th><th>부채비율</th><th>출처</th></tr></thead>
            <tbody>
              <tr v-for="f in supplier.financials" :key="f.year">
                <td>{{ f.year }}</td>
                <td class="num">{{ formatKrwCompact(f.revenue_krw) }}</td>
                <td class="num">{{ formatKrwCompact(f.operating_profit_krw) }}</td>
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
            <dt>결제 기간</dt><dd>{{ supplier.existing_contract.payment_days }}일</dd>
            <dt>연동 약정</dt><dd>{{ supplier.existing_contract.price_linkage ? '있음' : '없음' }}</dd>
          </dl>
          <p v-else class="muted">기존 거래 없음</p>
        </div>
        <div class="info-box wide-box">
          <h3>최근 견적</h3>
          <div v-if="supplier.recent_quotes.length" class="table-wrap">
            <table>
              <thead><tr><th>견적일</th><th>공정</th><th>소재</th><th>단가</th></tr></thead>
              <tbody>
                <tr v-for="q in supplier.recent_quotes" :key="`${q.quoted_at}-${q.process}-${q.material}`">
                  <td>{{ q.quoted_at }}</td><td>{{ q.process }}</td><td>{{ q.material }}</td><td class="num">{{ formatKrw(q.unit_price_krw) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted">최근 견적 없음</p>
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
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}
.wide-box {
  grid-column: span 1;
}
.info-box table td,
.info-box table th {
  background: transparent;
}
</style>
