<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'

// 로그인 전 화면(07~09): 상단 띠 + 본문 + 푸터
const helpOpen = ref(false)
</script>

<template>
  <div class="public">
    <header class="topbar">
      <RouterLink :to="{ name: 'login' }" class="brand">NegoPilot</RouterLink>
      <nav class="top-links" aria-label="도움말">
        <RouterLink :to="{ name: 'login' }">처음으로</RouterLink>
        <button class="link-btn" type="button" :aria-expanded="helpOpen" aria-controls="help-note" @click="helpOpen = !helpOpen">
          도움이 필요하신가요?
        </button>
      </nav>
    </header>
    <p v-if="helpOpen" id="help-note" class="help" role="status">
      계정 가입·로그인 문의는 소속 회사 관리자에게 연락해 주세요. 회사 코드도 관리자에게 받을 수 있습니다.
    </p>

    <main class="public-body">
      <RouterView />
    </main>

    <footer class="footer">
      <span>NegoPilot · 구매 협상 지원 서비스</span>
      <span class="footer-links">
        <span>이용약관</span>
        <span>개인정보 처리방침</span>
        <span>문의하기</span>
      </span>
    </footer>
  </div>
</template>

<style scoped>
.public {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}
.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  height: 56px;
  padding: 0 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.brand {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  text-decoration: none;
}
.top-links {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: clamp(24px, 12vw, 130px);
  padding-right: clamp(0px, 8vw, 110px);
  font-size: 12px;
}
.top-links a {
  text-decoration: none;
}
.help {
  margin: 0;
  padding: 10px 24px;
  font-size: 12px;
  color: var(--text-2);
  background: var(--primary-soft);
  border-bottom: 1px solid var(--border);
}
.public-body {
  flex: 1;
  padding: 40px 24px;
}
.footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 24px;
  padding: 18px 24px;
  background: var(--surface);
  border-top: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-3);
}
.footer-links {
  margin-left: auto;
  margin-right: clamp(0px, 24vw, 340px);
  display: flex;
  gap: 12px;
}
@media (max-width: 640px) {
  .top-links {
    gap: 16px;
  }
  .footer-links {
    margin: 0;
  }
}
</style>
