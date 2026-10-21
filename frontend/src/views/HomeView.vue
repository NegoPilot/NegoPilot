<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { LAW_COUNT, SEED_RFQS, SEED_SUPPLIER_COUNT } from '@/data/rfqs'
import { formatInteger, formatKrw } from '@/utils/format'

const router = useRouter()

const today = new Intl.DateTimeFormat('sv-SE').format(new Date())
const question = ref('')

const QUICK_QUESTIONS = [
  '이 RFQ에 적합한 협력사 3곳 추천해줘.',
  '기업의 재무 상태를 비교하려면 어떤 정보가 필요해?',
  '단가 인하 시 적용 법령을 찾아줘.',
  '지급기한 75일의 위험을 검사해줘.',
]

// 질문은 개인 AI 대화에서 이어진다
function openAssistant(text: string) {
  const query: Record<string, string> = {}
  if (text.trim()) query.q = text.trim()
  router.push({ name: 'chat', query })
}
</script>

<template>
  <header class="page-header">
    <h1>오늘 할 일</h1>
    <span class="muted">MVP 데이터 · {{ today }}</span>
    <span class="right">RFQ {{ SEED_RFQS.length }}건 · 협력사 {{ SEED_SUPPLIER_COUNT }}곳 · 법령 {{ LAW_COUNT }}개</span>
  </header>

  <main class="page-body">
    <form class="ask" @submit.prevent="openAssistant(question)">
      <label for="home-ask" class="ask-label">AI 도우미에게 묻기</label>
      <div class="ask-row">
        <input id="home-ask" v-model="question" type="text" placeholder="예: RFQ-2026-001의 후보 협력사를 추천해줘." />
        <button class="btn btn-primary" type="submit">질문하기</button>
      </div>
    </form>

    <section class="panel panel-pad" aria-labelledby="rfq-h">
      <div class="panel-title">
        <div>
          <h2 id="rfq-h">RFQ 목록 ({{ SEED_RFQS.length }})</h2>
          <p class="sub">business-mcp · list_rfqs() · 시드 데이터</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>RFQ 번호</th>
              <th>품목</th>
              <th>공정 / 소재</th>
              <th>연간 물량</th>
              <th>목표 단가</th>
              <th>조회</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in SEED_RFQS" :key="r.rfq_id">
              <td class="rfq-id">{{ r.rfq_id }}</td>
              <td>{{ r.part_name }}</td>
              <td>{{ r.process }} / {{ r.material }}</td>
              <td class="num">{{ formatInteger(r.annual_volume) }} EA</td>
              <td class="num">{{ formatKrw(r.target_price) }}</td>
              <td>
                <RouterLink
                  :to="{ name: 'suppliers', query: { rfq: r.rfq_id, process: r.process, material: r.material } }"
                  class="detail-link"
                >
                  상세 →
                  <span class="sr-only">{{ r.rfq_id }} 협력사 찾기</span>
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div class="two-col">
      <section class="panel panel-pad" aria-labelledby="quick-h">
        <div class="panel-title">
          <div>
            <h2 id="quick-h">AI 빠른 질문</h2>
            <p class="sub">버튼을 누르면 개인 AI 대화에서 이어집니다.</p>
          </div>
        </div>
        <div class="quick-list">
          <button v-for="q in QUICK_QUESTIONS" :key="q" class="quick" type="button" @click="openAssistant(q)">
            {{ q }}
          </button>
        </div>
      </section>

      <section class="panel panel-pad" aria-labelledby="data-h">
        <div class="panel-title">
          <div>
            <h2 id="data-h">조회 가능한 데이터</h2>
            <p class="sub">데이터 출처와 구현된 MCP 도구만 표시합니다.</p>
          </div>
        </div>
        <div class="source-list">
          <div class="source">
            <strong>business-mcp</strong>
            <span class="muted">RFQ · 협력사 · 품질 · 재무 · 견적 · 검사</span>
          </div>
          <div class="source">
            <strong>law-mcp</strong>
            <span class="muted">법령 검색 · 조문 · 시행일 · 인용 출처</span>
          </div>
        </div>
      </section>
    </div>

    <p class="footnote">* 이 홈 화면의 RFQ와 수치는 예시 데이터입니다. AI 대화의 자료 출처는 각 답변의 근거에서 확인하세요.</p>
  </main>
</template>

<style scoped>
.ask {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ask-label {
  font-size: 13px;
  font-weight: 600;
}
.ask-row {
  display: flex;
  gap: 10px;
}
.ask-row input {
  flex: 1;
}
.ask-row .btn {
  min-width: 104px;
}
.rfq-id {
  font-weight: 600;
}
.detail-link {
  color: var(--text-2);
  text-decoration: none;
}
.detail-link:hover {
  color: var(--primary);
}
.two-col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 20px;
}
.quick-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.quick {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 2px;
  background: var(--surface);
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.quick:nth-child(even) {
  background: var(--surface-2);
}
.quick:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.source-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.source {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 2px;
  background: var(--surface-2);
}
</style>
