<script setup lang="ts">
import type { AdminSupplierDetail } from '@/api/admin-suppliers'
import PanelSection from '@/components/form/PanelSection.vue'
import { formatKrwCompact, formatPercent } from '@/utils/format'

// 거래 조건과 재무. 관리자 API는 조회만 제공한다
defineProps<{ detail: AdminSupplierDetail }>()
</script>

<template>
  <PanelSection title="거래 조건·재무" sub="조회만 합니다. 거래 조건은 거래 이력이 있는 협력사만, 재무는 DART·가상 자료만 있습니다.">
    <h3>기존 거래 조건</h3>
    <dl v-if="detail.contract" class="fields">
      <div><dt>대금 지급기한</dt><dd>{{ detail.contract.payment_days }}일</dd></div>
      <div><dt>납품대금 연동 약정</dt><dd>{{ detail.contract.price_linkage ? '있음' : '없음' }}</dd></div>
      <div><dt>원재료 비중</dt><dd>{{ formatPercent(detail.contract.raw_material_ratio * 100) }}</dd></div>
      <div><dt>자동 갱신</dt><dd>{{ detail.contract.auto_renewal ? '예' : '아니요' }}</dd></div>
    </dl>
    <p v-else class="muted">기존 거래 조건 없음</p>

    <h3>연도별 재무</h3>
    <div v-if="detail.financials.length" class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">사업연도</th><th scope="col">구분</th><th scope="col">출처</th><th scope="col">매출액</th><th scope="col">영업이익</th>
            <th scope="col">당기순이익</th><th scope="col">자산총계</th><th scope="col">부채총계</th><th scope="col">자본총계</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in detail.financials" :key="f.bsns_year">
            <td class="num">{{ f.bsns_year }}</td>
            <td>{{ f.fs_div === 'CFS' ? '연결' : '별도' }}</td>
            <td>{{ f.source }}</td>
            <td class="num">{{ formatKrwCompact(f.revenue) }}</td>
            <td class="num">{{ formatKrwCompact(f.operating_income) }}</td>
            <td class="num">{{ formatKrwCompact(f.net_income) }}</td>
            <td class="num">{{ formatKrwCompact(f.total_assets) }}</td>
            <td class="num">{{ formatKrwCompact(f.total_liabilities) }}</td>
            <td class="num">{{ formatKrwCompact(f.total_equity) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="muted">재무 정보 없음</p>
  </PanelSection>
</template>

<style scoped>
h3 {
  margin: 0 0 8px;
  font-size: 13px;
}
h3:not(:first-child) {
  margin-top: 16px;
}
.fields {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
}
.fields dt {
  font-size: 11px;
  color: var(--text-3);
}
.fields dd {
  margin: 2px 0 0;
  font-size: 13px;
}
.muted {
  margin: 0;
}
@media (max-width: 760px) {
  .fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
