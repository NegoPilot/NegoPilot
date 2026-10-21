<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import AiAssistantPanel from '@/components/AiAssistantPanel.vue'
import AdminLayout from '@/layouts/AdminLayout.vue'
import PublicLayout from '@/layouts/PublicLayout.vue'
import { describeError } from '@/utils/errors'
import {
  assistantConversationId,
  assistantOpen,
  assistantQuestion,
  closeAssistant,
  setAssistantConversation,
} from '@/stores/assistant'
import { session, signOut } from '@/stores/session'

const route = useRoute()
const router = useRouter()

const logoutError = ref<string | null>(null)
async function logout() {
  try { await signOut(); await router.push({ name: 'login' }) }
  catch (error) { logoutError.value = describeError(error).message }
}
// 근거 자료 상세는 새 탭에서 단독으로 열리는 화면이라 사이드바를 그리지 않는다
const standalone = computed(() => route.meta.standalone === true)
</script>

<template>
  <RouterView v-if="standalone" />
  <PublicLayout v-else-if="route.meta.layout === 'public'" />
  <AdminLayout v-else-if="route.meta.layout === 'admin'" />
  <div v-else class="shell">
    <nav class="sidebar" aria-label="주 메뉴">
      <div class="brand">
        <span class="brand-name">NegoPilot</span>
        <span class="brand-team">{{ session?.department ?? '구매 업무 도우미' }}</span>
      </div>
      <RouterLink to="/overview" class="nav-item" exact-active-class="on">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
          <path d="M2 7.2L8 2l6 5.2V14h-4v-4H6v4H2z" />
        </svg>
        홈
      </RouterLink>
      <RouterLink to="/suppliers" class="nav-item" active-class="on">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
          <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
        </svg>
        협력사
      </RouterLink>
      <RouterLink to="/review" class="nav-item" active-class="on">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
          <path d="M8 1.8L14.2 8 8 14.2 1.8 8z" />
        </svg>
        법규 검토
      </RouterLink>
      <div v-if="session" class="account">
        <span class="account-email" :title="session.email">{{ session.email }}</span>
        <RouterLink v-if="session.role === 'ADMIN'" :to="{ name: 'admin' }" class="btn account-btn">관리자 화면 →</RouterLink>
        <p v-if="logoutError" class="field-error" role="alert">{{ logoutError }}</p>
        <button class="btn account-btn" type="button" @click="logout">로그아웃</button>
      </div>
    </nav>
    <div class="content">
      <RouterView />
    </div>
    <div v-if="assistantOpen" class="ai-drawer">
      <AiAssistantPanel
        :initial-question="assistantQuestion"
        :rfq="null"
        :conversation-id="assistantConversationId"
        :back-path="route.fullPath"
        closable
        @open="setAssistantConversation"
        @close="closeAssistant"
      />
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-wrap: wrap;
  min-height: 100vh;
}
.sidebar {
  flex: 0 0 196px;
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  padding: 20px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.brand {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 10px 24px;
}
.brand-name {
  font-size: 17px;
  font-weight: 700;
}
.brand-team {
  font-size: 11px;
  color: var(--text-3);
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 38px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: var(--radius);
  color: var(--text-2);
  text-decoration: none;
  font-size: 13px;
}
.nav-item:hover {
  background: #e7e9ec;
  color: var(--text);
}
.nav-item.on {
  background: var(--surface);
  border-color: var(--border);
  color: var(--primary);
  font-weight: 600;
}
.account {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 24px 0 0;
}
.account-email {
  padding: 0 10px;
  font-size: 11px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-btn {
  width: 100%;
  height: 34px;
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  border-color: var(--border);
}
.content {
  flex: 999 1 640px;
  min-width: 0;
  background: var(--bg);
}
/* 보고 있는 화면 위 오른쪽에 띄운다. 페이지는 그대로 둔다 */
.ai-drawer {
  position: fixed;
  top: 16px;
  right: 16px;
  bottom: 16px;
  z-index: 50;
  display: flex;
  width: min(420px, calc(100vw - 32px));
  box-shadow: 0 8px 28px rgba(30, 50, 90, 0.22);
}
.ai-drawer :deep(.ai-panel) {
  position: static;
  flex: 1;
  min-height: 0;
  max-height: none;
}
@media (max-width: 760px) {
  .sidebar {
    flex: 1 1 100%;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
  }
  .brand {
    padding: 0 10px;
  }
  .account {
    margin: 0 0 0 auto;
    padding: 0;
    flex-direction: row;
    align-items: center;
  }
  .account-btn {
    width: auto;
  }
}
</style>
