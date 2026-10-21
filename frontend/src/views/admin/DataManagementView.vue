<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { SEED_SUPPLIER_MAX, STORED_LAWS } from '@/data/dataCatalog'
import { downloadJson, useDataRequests } from '@/stores/dataRequests'

const { state, remove } = useDataRequests()

const versionCount = STORED_LAWS.reduce((n, l) => n + l.versions.length, 0)
const kpis = computed(() => [
  { label: '협력사', value: `${SEED_SUPPLIER_MAX}곳`, sub: 'business-mcp 시드 (DART + 가상)' },
  { label: '법령', value: `${STORED_LAWS.length}개`, sub: `저장된 판 ${versionCount}개` },
  { label: '등록 요청', value: `${state.items.length}건`, sub: '이 화면에서 만든 요청' },
])
const KIND_LABEL = { supplier: '협력사', law: '법령' } as const
</script>

<template>
  <main class="page-body">
    <p class="intro">협상 Agent가 조회하는 협력사 DB와 법령 원문을 추가합니다.</p>

    <div class="kpis">
      <div v-for="k in kpis" :key="k.label" class="panel kpi-card">
        <span class="label">{{ k.label }}</span>
        <strong class="value">{{ k.value }}</strong>
        <span class="sub">{{ k.sub }}</span>
      </div>
    </div>

    <div class="actions">
      <RouterLink :to="{ name: 'admin-supplier-new' }" class="panel action">
        <strong>협력사 등록 →</strong>
        <span>기본 정보, 공정별 생산능력, 품질·납기, 거래 조건, 견적, 재무</span>
        <span class="muted">business-mcp · PostgreSQL</span>
      </RouterLink>
      <RouterLink :to="{ name: 'admin-law-new' }" class="panel action">
        <strong>법령 등록 →</strong>
        <span>legalize-kr에서 새 법령 받기, 개정본 원문 올리기</span>
        <span class="muted">law-mcp · 원문 파일 색인</span>
      </RouterLink>
    </div>

    <section class="panel panel-pad" aria-labelledby="req-h">
      <div class="panel-title">
        <div>
          <h2 id="req-h">등록 요청</h2>
          <p class="sub">등록 API 연결 전이라 DB에 바로 저장되지 않습니다. JSON을 내려받아 반영합니다.</p>
        </div>
      </div>
      <div v-if="state.items.length" class="table-wrap">
        <table>
          <thead>
            <tr><th>종류</th><th>대상</th><th>내용</th><th>요청</th><th>상태</th><th>처리</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in state.items" :key="r.id">
              <td>{{ KIND_LABEL[r.kind] }}</td>
              <td><code>{{ r.target }}</code></td>
              <td>{{ r.summary }}</td>
              <td class="num">{{ r.requestedAt }} · {{ r.requestedBy }}</td>
              <td><span class="badge badge-gray">API 연결 전</span></td>
              <td class="ops">
                <button class="link-btn" type="button" @click="downloadJson(`${r.kind}-${r.target}.json`, r.payload)">JSON</button>
                <button class="link-btn danger" type="button" @click="remove(r.id)">삭제</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted">아직 등록 요청이 없습니다.</p>
    </section>

    <p class="footnote">등록 요청은 새로고침하면 사라집니다. 협력사 수와 법령 목록은 저장소의 시드·원문 기준입니다.</p>
  </main>
</template>

<style scoped>
.intro {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}
.kpi-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px;
}
.kpi-card .label {
  font-size: 12px;
  color: var(--text-2);
}
.kpi-card .value {
  font-size: 22px;
}
.kpi-card .sub {
  font-size: 11px;
  color: var(--text-3);
}
.actions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px;
}
.action {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 18px 16px;
  color: var(--text);
  text-decoration: none;
  font-size: 12px;
}
.action strong {
  font-size: 15px;
  color: var(--primary);
}
.action:hover {
  border-color: var(--primary);
  color: var(--text);
}
code {
  font-size: 12px;
}
.ops {
  display: flex;
  gap: 12px;
  white-space: nowrap;
}
.danger {
  color: var(--danger);
}
.footnote {
  margin: 0;
}
</style>
