<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { useAdminStore } from '@/stores/admin'

const { state, summary } = useAdminStore()

const kpis = computed(() => [
  { label: '전체 회원', value: `${summary.value.total}명`, sub: `활성 계정 ${summary.value.active}명` },
  { label: '가입 승인 대기', value: `${summary.value.pending}건`, sub: '확인이 필요한 신규 신청' },
  { label: '구매팀장', value: `${summary.value.leads}명`, sub: '팀별 승인 담당자' },
  { label: '이용 정지', value: `${summary.value.suspended}명`, sub: '접근이 제한된 계정' },
])
const requests = computed(() => state.requests.slice(0, 3))
const logs = computed(() => state.logs.slice(0, 3))
</script>

<template>
  <main class="page-body">
    <p class="intro">조직의 가입 요청과 계정 접근 상태를 확인하세요.</p>

    <div class="kpis">
      <div v-for="k in kpis" :key="k.label" class="panel kpi-card">
        <span class="label">{{ k.label }}</span>
        <strong class="value">{{ k.value }}</strong>
        <span class="sub">{{ k.sub }}</span>
      </div>
    </div>

    <section class="panel panel-pad" aria-labelledby="req-h">
      <div class="panel-title">
        <div>
          <h2 id="req-h">가입 승인 요청</h2>
          <p class="sub">회사와 부서를 확인한 뒤 기본 역할로 승인해 주세요.</p>
        </div>
        <RouterLink :to="{ name: 'admin-approvals' }" class="more">전체 보기 →</RouterLink>
      </div>
      <div v-if="requests.length" class="table-wrap">
        <table>
          <thead>
            <tr><th>신청자</th><th>회사 이메일</th><th>부서</th><th>신청일</th><th>상태</th><th>처리</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in requests" :key="r.email">
              <td>{{ r.name }}</td>
              <td>{{ r.email }}</td>
              <td>{{ r.department }}</td>
              <td class="num">{{ r.requestedAt }}</td>
              <td>승인 대기</td>
              <td><RouterLink :to="{ name: 'admin-members', query: { request: r.email } }" class="act">상세 확인 →</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted">대기 중인 가입 신청이 없습니다.</p>
    </section>

    <section class="panel panel-pad" aria-labelledby="log-h">
      <div class="panel-title">
        <h2 id="log-h">최근 관리 이력</h2>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>일시</th><th>처리자</th><th>내용</th><th>대상</th></tr>
          </thead>
          <tbody>
            <tr v-for="(l, i) in logs" :key="`${l.at}-${i}`">
              <td class="num">{{ l.at }}</td>
              <td>{{ l.actor }}</td>
              <td>{{ l.action }}</td>
              <td>{{ l.target }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <p class="footnote">화면에 표시된 계정과 관리 이력은 설계용 예시입니다. 새로고침하면 처음 상태로 돌아갑니다.</p>
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
  font-variant-numeric: tabular-nums;
}
.kpi-card .sub {
  font-size: 11px;
  color: var(--text-3);
}
.more,
.act {
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
}
.act {
  color: var(--text);
  font-weight: 400;
}
.act:hover {
  color: var(--primary);
}
.footnote {
  margin: 0;
}
</style>
