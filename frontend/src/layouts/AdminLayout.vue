<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { CURRENT_ADMIN, ORGANIZATION } from '@/data/accounts'
import { signOut } from '@/stores/session'

// 관리자 화면(10~11): 업무 화면과 다른 사이드바를 쓴다
const route = useRoute()
const router = useRouter()

function logout() {
  signOut()
  router.push({ name: 'login' })
}
const title = computed(() => route.meta.title ?? '관리자')

const NAV = [
  { name: 'admin', label: '관리 현황' },
  { name: 'admin-members', label: '회원·권한 관리' },
  { name: 'admin-approvals', label: '가입 승인' },
  { name: 'admin-history', label: '접근 이력' },
] as const

const DATA_NAV = [
  { name: 'admin-data', label: '데이터 현황' },
  { name: 'admin-supplier-new', label: '협력사 등록' },
  { name: 'admin-law-new', label: '법령 등록' },
] as const
</script>

<template>
  <div class="shell">
    <nav class="sidebar" aria-label="관리자 메뉴">
      <div class="brand">
        <span class="brand-name">NegoPilot</span>
        <span class="brand-team">{{ ORGANIZATION.name }} · 관리자</span>
      </div>
      <RouterLink v-for="n in NAV" :key="n.name" :to="{ name: n.name }" class="nav-item" exact-active-class="on">
        {{ n.label }}
      </RouterLink>
      <p class="nav-group">데이터 관리</p>
      <RouterLink v-for="n in DATA_NAV" :key="n.name" :to="{ name: n.name }" class="nav-item" exact-active-class="on">
        {{ n.label }}
      </RouterLink>
      <div class="side-bottom">
        <RouterLink :to="{ name: 'home' }" class="btn side-btn">업무 화면으로 →</RouterLink>
        <button class="btn side-btn plain" type="button" @click="logout">로그아웃</button>
      </div>
    </nav>

    <div class="content">
      <header class="page-header">
        <h1>{{ title }}</h1>
        <span class="right">
          <span class="badge">관리자</span>
          <span>{{ CURRENT_ADMIN.name }} · {{ CURRENT_ADMIN.department }}</span>
        </span>
      </header>
      <RouterView />
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
  gap: 6px;
}
.brand {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 10px 28px;
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
  height: 36px;
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
  color: var(--text);
  font-weight: 600;
}
.nav-group {
  margin: 18px 0 2px;
  padding: 0 12px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-3);
}
.side-bottom {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 24px;
}
.side-btn {
  width: 100%;
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  border-color: var(--border);
}
.side-btn.plain {
  color: var(--primary);
}
.content {
  flex: 999 1 640px;
  min-width: 0;
  background: var(--bg);
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
  .side-bottom {
    margin: 0;
    padding: 0;
    flex-direction: row;
  }
  .side-btn {
    width: auto;
  }
}
</style>
