<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { RELATED_ARTICLES, SAMPLE_OFFER, SAMPLE_ROWS, type Severity } from '@/data/lawReview'
import { formatKrw } from '@/utils/format'

type TabKey = 'result' | 'input' | 'laws'
const TABS: { key: TabKey; label: string }[] = [
  { key: 'result', label: '검토 결과' },
  { key: 'input', label: '조건 입력' },
  { key: 'laws', label: '관련 법령' },
]
const tab = ref<TabKey>('result')

const SEVERITY_LABEL: Record<Severity, string> = {
  violation: '위반 소지',
  warning: '확인 필요',
  info: '안내',
}

const counts = computed(() => ({
  violation: SAMPLE_ROWS.filter((r) => r.severity === 'violation').length,
  warning: SAMPLE_ROWS.filter((r) => r.severity === 'warning').length,
  info: SAMPLE_ROWS.filter((r) => r.severity === 'info').length,
}))

const form = reactive({
  unitPrice: `${SAMPLE_OFFER.unitPrice}`,
  paymentDays: `${SAMPLE_OFFER.paymentDays}`,
  priceLinkage: SAMPLE_OFFER.priceLinkage ? 'yes' : 'no',
  rawMaterialRatio: `${SAMPLE_OFFER.rawMaterialRatio}`,
})
</script>

<template>
  <header class="page-header">
    <h1>법규 검토</h1>
    <span class="context">business-mcp.check_offer → law-mcp.get_article</span>
    <span class="right"><span class="badge badge-gray">검토 전 · 시안</span></span>
  </header>

  <section class="target" aria-labelledby="target-h">
    <h2 id="target-h">검사 대상 · {{ SAMPLE_OFFER.rfq }} / 선택 협력사</h2>
    <dl class="target-kv">
      <div><dt>업체</dt><dd>{{ SAMPLE_OFFER.supplier }}</dd></div>
      <div><dt>제안 단가</dt><dd>{{ formatKrw(SAMPLE_OFFER.unitPrice) }}</dd></div>
      <div><dt>지급기한</dt><dd>{{ SAMPLE_OFFER.paymentDays }}일</dd></div>
      <div><dt>원재료 비중</dt><dd>{{ SAMPLE_OFFER.rawMaterialRatio }}%</dd></div>
      <div><dt>가격 연동</dt><dd>{{ SAMPLE_OFFER.priceLinkage ? '포함' : '미포함' }}</dd></div>
    </dl>
    <div class="tabs" role="tablist" aria-label="법규 검토">
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
  </section>

  <main class="page-body">
    <section v-if="tab === 'result'" class="panel panel-pad" aria-labelledby="risk-h">
      <div class="panel-title">
        <div>
          <h2 id="risk-h">위험 검토 결과</h2>
          <p class="sub">검사 예시 · 실제 판정은 check_offer 응답으로 갱신</p>
        </div>
        <div class="counts">
          <span class="sev sev-violation">위반 소지 {{ counts.violation }}</span>
          <span class="sev sev-warning">확인 필요 {{ counts.warning }}</span>
          <span class="sev sev-info">안내 {{ counts.info }}</span>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>상태</th><th>검토 조건</th><th>검사 결과</th><th>법령 근거</th><th>조치</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in SAMPLE_ROWS" :key="r.code">
              <td class="state" :class="`sev-${r.severity}`">{{ SEVERITY_LABEL[r.severity] }}</td>
              <td class="cond" :title="r.code">{{ r.condition }}</td>
              <td>{{ r.result }}</td>
              <td>{{ r.basis }}</td>
              <td>{{ r.action }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="footnote">* 위험 신호는 법률 판단의 확정이 아닙니다. 적용 조건과 시행일을 반드시 확인해야 합니다.</p>
    </section>

    <section v-if="tab !== 'laws'" class="panel panel-pad" aria-labelledby="recheck-h">
      <div class="panel-title">
        <div>
          <h2 id="recheck-h">검사 조건 확인 및 재검사</h2>
          <p class="sub">선택한 RFQ/협력사 조건을 수정하고 규칙 검사를 다시 실행합니다.</p>
        </div>
      </div>
      <form class="recheck" @submit.prevent>
        <div class="field">
          <label for="r-price">제안 단가 (원)</label>
          <input id="r-price" v-model="form.unitPrice" type="text" inputmode="numeric" />
        </div>
        <div class="field">
          <label for="r-days">지급기한 (일)</label>
          <input id="r-days" v-model="form.paymentDays" type="text" inputmode="numeric" />
        </div>
        <div class="field">
          <label for="r-linkage">연동 약정</label>
          <select id="r-linkage" v-model="form.priceLinkage">
            <option value="no">아니오</option>
            <option value="yes">예</option>
          </select>
        </div>
        <div class="field">
          <label for="r-ratio">원재료 비중 (%)</label>
          <input id="r-ratio" v-model="form.rawMaterialRatio" type="text" inputmode="numeric" />
        </div>
        <button class="btn btn-primary" type="submit" disabled aria-describedby="recheck-note">조건 수정 후 재검사</button>
      </form>
      <p id="recheck-note" class="footnote">재검사는 협상안 검사(check_offer) HTTP API가 연결되면 동작합니다.</p>
    </section>

    <section v-if="tab === 'laws'" class="panel panel-pad" aria-labelledby="laws-h">
      <div class="panel-title">
        <div>
          <h2 id="laws-h">관련 법령</h2>
          <p class="sub">law-mcp 색인 · 하도급법 시행 2026-10-02 판 기준 요지</p>
        </div>
      </div>
      <ul class="laws">
        <li v-for="a in RELATED_ARTICLES" :key="a.citation">
          <strong>{{ a.citation }}</strong>
          <p>{{ a.summary }}</p>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.target {
  padding: 18px 24px 0;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.target h2 {
  font-size: 17px;
}
.target-kv {
  margin: 18px 0 14px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}
.target-kv dt {
  font-size: 11px;
  color: var(--text-3);
}
.target-kv dd {
  margin: 4px 0 0;
  font-size: 14px;
}
.target .tabs {
  border-bottom: 0;
}
.counts {
  display: flex;
  gap: 40px;
  font-size: 11px;
  font-weight: 500;
}
.sev-violation {
  color: var(--danger);
}
.sev-warning {
  color: var(--warning);
}
.sev-info {
  color: var(--success);
}
.state {
  font-weight: 700;
}
.state.sev-info {
  color: var(--text);
}
.cond {
  color: var(--text-2);
}
.footnote {
  margin: 10px 0 0;
}
.recheck {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr)) minmax(180px, 1.3fr);
  gap: 16px;
  align-items: end;
}
.laws {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.laws li {
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
}
.laws p {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--text-2);
}
@media (max-width: 900px) {
  .recheck {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
