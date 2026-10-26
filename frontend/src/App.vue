<script setup lang="ts">
import { computed, ref, watch } from 'vue'
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

// 사이드바와 각 화면의 상단 메뉴(.page-header)는 관리자 화면처럼 고정한다. 상단 메뉴 높이를 CSS 변수로 알려
// 화면 안 이동(scrollIntoView)이 상단 메뉴에 가리지 않게 한다. 화면이 바뀌면 새 화면의 상단 메뉴를 다시 잰다
const content = ref<HTMLElement | null>(null)
const setHeaderHeight = (px: number | null) => px === null
  ? document.documentElement.style.removeProperty('--page-header-height')
  : document.documentElement.style.setProperty('--page-header-height', `${px}px`)
const headerSize = new ResizeObserver(([entry]) => setHeaderHeight(entry?.borderBoxSize[0]?.blockSize ?? 0))
function observeHeader() {
  headerSize.disconnect()
  const header = content.value?.querySelector(':scope > .page-header')
  if (header) headerSize.observe(header)
  else setHeaderHeight(null)
}
const contentChanges = new MutationObserver(observeHeader)
watch(content, el => {
  contentChanges.disconnect()
  if (el) contentChanges.observe(el, { childList: true })
  observeHeader()
})
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
        <svg class="nav-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2.5 7L8 2.5 13.5 7v6.5h-3.5v-4h-4v4H2.5z" />
        </svg>
        홈
      </RouterLink>
      <RouterLink to="/suppliers" class="nav-item" active-class="on">
        <svg class="nav-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2.5 13.5V7.5l3.5 2V7.5l3.5 2v-6.5h4v10.5zM5 11.5h1M8.5 11.5h1" />
        </svg>
        협력사
      </RouterLink>
      <RouterLink to="/review/offer" class="nav-item" active-class="on">
        <svg class="nav-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4 1.5h5.5L12.5 4.5v10h-8.5zM9.5 1.5v3h3M6 8h4.5M6 10.5h4.5" />
        </svg>
        협상안 검토
      </RouterLink>
      <div v-if="session" class="account">
        <span class="account-email" :title="session.email">{{ session.email }}</span>
        <RouterLink v-if="session.role === 'ADMIN'" :to="{ name: 'admin' }" class="btn account-btn">관리자 화면 →</RouterLink>
        <p v-if="logoutError" class="field-error" role="alert">{{ logoutError }}</p>
        <button class="btn account-btn" type="button" @click="logout">로그아웃</button>
      </div>
    </nav>
    <div ref="content" class="content">
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
/* 메뉴 아이콘은 같은 선 굵기와 둥근 끝으로 맞춘다 */
.nav-icon {
  flex: none;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.shell {
  display: flex;
  flex-wrap: wrap;
  min-height: 100vh;
}
.sidebar {
  position: sticky;
  top: 0;
  align-self: flex-start;
  height: 100vh;
  overflow-y: auto;
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
.content > :deep(.page-header) {
  position: sticky;
  top: 0;
  z-index: 20;
}
/* 보고 있는 화면 위 오른쪽에 띄운다. 페이지는 그대로 둔다 */
.ai-drawer {
  position: fixed;
  top: 16px;
  right: 16px;
  bottom: 16px;
  z-index: 50;
  display: flex;
  /* 대화 목록을 펼치면 패널 내용만큼 왼쪽으로 넓어진다 */
  max-width: calc(100vw - 32px);
  /* 그림자도 패널과 같은 모서리로 그려야 아래 모서리가 각져 보이지 않는다 */
  border-radius: var(--radius);
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
    position: static;
    height: auto;
    overflow-y: visible;
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
