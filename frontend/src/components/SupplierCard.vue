<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import type { SupplierCard } from '@/api/types'
import { formatKrwCompact, formatPercent, formatPpm } from '@/utils/format'

const props = defineProps<{
  card: SupplierCard
  conversationId: string
  responseId: string
}>()

const router = useRouter()

const latestFinancial = computed(() => {
  const list = [...props.card.supplier.financials].sort((a, b) => b.year - a.year)
  return list[0] ?? null
})

// 근거 자료 상세는 새 탭에서 연다. 새 탭은 이 URL만으로 대화를 다시 불러온다
const evidenceLinks = computed(() =>
  props.card.evidence_ids.map((evidenceId) => ({
    evidenceId,
    href: router.resolve({
      name: 'evidence-detail',
      params: { conversationId: props.conversationId, responseId: props.responseId, evidenceId },
    }).href,
  })),
)
</script>

<template>
  <article class="supplier-card">
    <header class="card-head">
      <div>
        <h3>{{ card.supplier.name }}</h3>
        <span class="muted">{{ card.supplier.supplier_id }} · {{ card.supplier.region }}</span>
      </div>
      <span class="badge">{{ card.supplier.iatf16949 ? 'IATF 16949 인증' : 'IATF 16949 없음' }}</span>
    </header>
    <dl class="metrics">
      <div><dt>불량률</dt><dd>{{ formatPpm(card.supplier.quality.defect_ppm) }}</dd></div>
      <div><dt>납기 준수율</dt><dd>{{ formatPercent(card.supplier.quality.on_time_delivery_rate) }}</dd></div>
      <div>
        <dt>매출{{ latestFinancial ? ` (${latestFinancial.year})` : '' }}</dt>
        <dd>{{ formatKrwCompact(latestFinancial?.revenue_krw) }}</dd>
      </div>
      <div>
        <dt>기존 결제 기간</dt>
        <dd>{{ card.supplier.existing_contract ? `${card.supplier.existing_contract.payment_days}일` : '기존 거래 없음' }}</dd>
      </div>
    </dl>
    <footer class="card-foot">
      <a
        v-for="link in evidenceLinks"
        :key="link.evidenceId"
        :href="link.href"
        target="_blank"
        rel="noopener noreferrer"
        class="evidence-link"
      >
        근거 자료 상세
        <span class="sr-only">(새 탭에서 열림)</span>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
          <path d="M5 2H2v8h8V7M7 2h3v3M10 2L5.5 6.5" />
        </svg>
      </a>
      <RouterLink :to="{ name: 'supplier-detail', params: { supplierId: card.supplier.supplier_id } }">협력사 상세</RouterLink>
    </footer>
  </article>
</template>

<style scoped>
.supplier-card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.card-head h3 {
  font-size: 15px;
}
.badge {
  font-size: 12px;
  color: var(--text-2);
  border: 1px solid var(--border-strong);
  border-radius: 2px;
  padding: 2px 6px;
  white-space: nowrap;
}
.metrics {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 8px 16px;
}
.metrics dt {
  font-size: 12px;
  color: var(--text-3);
}
.metrics dd {
  margin: 2px 0 0;
  font-variant-numeric: tabular-nums;
}
.card-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  padding-top: 10px;
  border-top: 1px solid #edeff1;
}
.evidence-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
</style>
