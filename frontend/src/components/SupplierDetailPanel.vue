<script setup lang="ts">
import { computed } from 'vue'

import type { SupplierDetail } from '@/api/types'
import {
  formatCapacity,
  formatKrw,
  formatKrwCompact,
  formatPercent,
  formatPpm,
} from '@/utils/format'

const props = defineProps<{ supplier: SupplierDetail }>()

const isStub = computed(() => Object.values(props.supplier.provenance).every((v) => v === 'STUB'))
</script>

<template>
  <div class="detail-grid">
    <section class="panel" aria-labelledby="d-basic">
      <div class="panel-head"><h2 id="d-basic">기본 정보</h2></div>
      <dl class="kv panel-body">
        <div><dt>협력사 ID</dt><dd>{{ supplier.supplier_id }}</dd></div>
        <div><dt>지역</dt><dd>{{ supplier.region }}</dd></div>
        <div><dt>IATF 16949</dt><dd>{{ supplier.iatf16949 ? '인증' : '없음' }}</dd></div>
        <div>
          <dt>기존 계약</dt>
          <dd v-if="supplier.existing_contract">
            결제 {{ supplier.existing_contract.payment_days }}일 · 연동 약정
            {{ supplier.existing_contract.price_linkage ? '있음' : '없음' }}
          </dd>
          <dd v-else>기존 거래 없음</dd>
        </div>
        <div><dt>데이터 출처</dt><dd>{{ isStub ? '테스트 데이터 (STUB)' : '공시·사내 데이터' }}</dd></div>
      </dl>
    </section>

    <section class="panel" aria-labelledby="d-quality">
      <div class="panel-head"><h2 id="d-quality">품질·납기</h2></div>
      <dl class="kv panel-body">
        <div><dt>불량률</dt><dd class="num-left">{{ formatPpm(supplier.quality.defect_ppm) }}</dd></div>
        <div><dt>납기 준수율</dt><dd class="num-left">{{ formatPercent(supplier.quality.on_time_delivery_rate) }}</dd></div>
      </dl>
    </section>

    <section class="panel wide" aria-labelledby="d-capability">
      <div class="panel-head"><h2 id="d-capability">생산능력</h2></div>
      <div v-if="supplier.capabilities.length" class="table-wrap">
        <table>
          <thead>
            <tr><th>공정</th><th>소재</th><th class="num">월 생산능력</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in supplier.capabilities" :key="`${c.process}-${c.material}`">
              <td>{{ c.process }}</td>
              <td>{{ c.material }}</td>
              <td class="num">{{ formatCapacity(c.monthly_capacity) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="panel-body muted">생산능력 정보 없음</p>
    </section>

    <section class="panel wide" aria-labelledby="d-financials">
      <div class="panel-head"><h2 id="d-financials">재무</h2></div>
      <div v-if="supplier.financials.length" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>연도</th>
              <th class="num">매출</th>
              <th class="num">영업이익</th>
              <th class="num">부채비율</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in supplier.financials" :key="f.year">
              <td>{{ f.year }}</td>
              <td class="num">{{ formatKrwCompact(f.revenue_krw) }}</td>
              <td class="num">{{ formatKrwCompact(f.operating_profit_krw) }}</td>
              <td class="num">{{ formatPercent(f.debt_ratio) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="panel-body muted">재무 정보 없음</p>
    </section>

    <section class="panel wide" aria-labelledby="d-quotes">
      <div class="panel-head"><h2 id="d-quotes">최근 견적</h2></div>
      <div v-if="supplier.recent_quotes.length" class="table-wrap">
        <table>
          <thead>
            <tr><th>견적일</th><th>공정</th><th>소재</th><th class="num">단가</th></tr>
          </thead>
          <tbody>
            <tr v-for="q in supplier.recent_quotes" :key="`${q.quoted_at}-${q.process}-${q.material}`">
              <td>{{ q.quoted_at }}</td>
              <td>{{ q.process }}</td>
              <td>{{ q.material }}</td>
              <td class="num">{{ formatKrw(q.unit_price_krw) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="panel-body muted">최근 견적 없음</p>
    </section>
  </div>
</template>

<style scoped>
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}
.wide {
  grid-column: 1 / -1;
}
.kv {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px 20px;
}
.kv dt {
  font-size: 12px;
  color: var(--text-3);
}
.kv dd {
  margin: 2px 0 0;
}
.num-left {
  font-variant-numeric: tabular-nums;
}
</style>
