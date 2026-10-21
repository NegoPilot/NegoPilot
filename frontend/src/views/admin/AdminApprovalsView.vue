<script setup lang="ts">
import { RouterLink } from 'vue-router'

import { useAdminStore } from '@/stores/admin'

// 와이어프레임에는 없는 화면. 관리 현황의 '전체 보기'와 사이드바 '가입 승인'이 여기로 온다
const { state } = useAdminStore()
</script>

<template>
  <main class="page-body">
    <p class="intro">승인을 기다리는 가입 신청 전체입니다. '상세 확인'에서 역할을 정해 승인하거나 반려합니다.</p>
    <section class="panel panel-pad" aria-labelledby="all-req-h">
      <div class="panel-title">
        <h2 id="all-req-h">가입 승인 요청 {{ state.requests.length }}건</h2>
      </div>
      <div v-if="state.requests.length" class="table-wrap">
        <table>
          <thead>
            <tr><th>신청자</th><th>회사 이메일</th><th>소속 회사</th><th>부서</th><th>직책</th><th>신청일</th><th>처리</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in state.requests" :key="r.email">
              <td>{{ r.name }}</td>
              <td>{{ r.email }}</td>
              <td>{{ r.company }}</td>
              <td>{{ r.department }}</td>
              <td>{{ r.position || '-' }}</td>
              <td class="num">{{ r.requestedAt }}</td>
              <td><RouterLink :to="{ name: 'admin-members', query: { request: r.email } }" class="act">상세 확인 →</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted">대기 중인 가입 신청이 없습니다.</p>
    </section>
    <p class="footnote">화면의 신청 정보는 설계용 예시입니다.</p>
  </main>
</template>

<style scoped>
.intro {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
.act {
  font-size: 12px;
  color: var(--text);
  text-decoration: none;
}
.act:hover {
  color: var(--primary);
}
.footnote {
  margin: 0;
}
</style>
