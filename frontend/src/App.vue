<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <header class="app-header">
    <RouterLink to="/suppliers" class="brand">NegoPilot</RouterLink>
    <nav aria-label="주 메뉴" class="app-nav">
      <RouterLink to="/suppliers">협력사 탐색</RouterLink>
      <RouterLink to="/chat">협상 도우미</RouterLink>
    </nav>
  </header>
  <!-- 계약 요구: 모든 화면에서 테스트 데이터와 템플릿 응답임을 표시한다 -->
  <p class="test-banner" role="note">
    테스트 데이터 · 모든 기업과 수치는 API 연결 검증용 가상 데이터이고, 답변은 정해진 템플릿입니다.
  </p>
  <RouterView />
</template>

<style scoped>
.app-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 24px;
  padding: 12px 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.brand {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  text-decoration: none;
}
.app-nav {
  display: flex;
  gap: 16px;
}
.app-nav a {
  color: var(--text-2);
  text-decoration: none;
  padding: 6px 0;
  border-bottom: 2px solid transparent;
}
.app-nav a.router-link-active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: 600;
}
.test-banner {
  margin: 0;
  padding: 8px 24px;
  font-size: 12px;
  color: var(--text-2);
  background: #fff8e6;
  border-bottom: 1px solid #f0e2bd;
}
</style>
