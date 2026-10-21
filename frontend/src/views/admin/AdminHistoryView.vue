<script setup lang="ts">
import { useAdminStore } from '@/stores/admin'

// 와이어프레임에는 없는 화면. 지금은 관리 이력만 보여 주고, 로그인 기록은 인증 API가 생기면 붙인다
const { state } = useAdminStore()
</script>

<template>
  <main class="page-body">
    <p class="intro">가입 승인, 역할 변경, 이용 정지 같은 관리 작업 기록입니다.</p>
    <section class="panel panel-pad" aria-labelledby="hist-h">
      <div class="panel-title">
        <div>
          <h2 id="hist-h">관리 이력</h2>
          <p class="sub">로그인·접속 기록은 인증 API가 연결되면 함께 표시합니다.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>일시</th><th>처리자</th><th>내용</th><th>대상</th></tr>
          </thead>
          <tbody>
            <tr v-for="(l, i) in state.logs" :key="`${l.at}-${i}`">
              <td class="num">{{ l.at }}</td>
              <td>{{ l.actor }}</td>
              <td>{{ l.action }}</td>
              <td>{{ l.target }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <p class="footnote">화면의 관리 이력은 설계용 예시입니다. 새로고침하면 처음 상태로 돌아갑니다.</p>
  </main>
</template>

<style scoped>
.intro {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
.footnote {
  margin: 0;
}
</style>
