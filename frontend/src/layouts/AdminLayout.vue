<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { describeError } from '@/utils/errors'
import { session, signOut } from '@/stores/session'

// 관리자 화면: 업무 화면과 다른 사이드바를 쓴다. 사이드바는 묶음 3개, 묶음 안 화면은 상단 탭으로 오간다
const route = useRoute()
const router = useRouter()

const logoutError = ref<string | null>(null)
async function logout() {
  try { await signOut(); await router.push({ name: 'login' }) }
  catch (error) { logoutError.value = describeError(error).message }
}

const GROUPS = [
  {
    label: '현황',
    tabs: [
      { name: 'admin', label: '관리 현황' },
      { name: 'admin-history', label: '접근 이력' },
      { name: 'admin-data', label: '데이터 현황' },
    ],
  },
  {
    label: '회원 관리',
    tabs: [
      { name: 'admin-members', label: '회원·권한 관리' },
      { name: 'admin-approvals', label: '가입 승인' },
    ],
  },
  {
    label: '등록',
    tabs: [
      { name: 'admin-supplier-new', label: '협력사 등록' },
      { name: 'admin-law-new', label: '법령 등록' },
    ],
  },
] as const

const group = computed(() => GROUPS.find((g) => g.tabs.some((t) => t.name === route.name)) ?? GROUPS[0])
const connected = computed(() => ['admin', 'admin-members', 'admin-approvals'].includes(String(route.name)))
</script>

<template>
  <div class="shell">
    <nav class="sidebar" aria-label="관리자 메뉴">
      <div class="brand">
        <span class="brand-name">NegoPilot</span>
        <span class="brand-team">관리자</span>
      </div>
      <RouterLink
        v-for="g in GROUPS"
        :key="g.label"
        :to="{ name: g.tabs[0].name }"
        class="nav-item"
        :class="{ on: g === group }"
        :aria-current="g === group ? 'page' : undefined"
      >
        {{ g.label }}
      </RouterLink>
      <div class="side-bottom">
        <RouterLink :to="{ name: 'home' }" class="btn side-btn">업무 화면으로 →</RouterLink>
        <button class="btn side-btn plain" type="button" @click="logout">로그아웃</button>
      </div>
    </nav>

    <div class="content">
      <header class="admin-header">
        <div class="page-header">
          <h1>{{ group.label }}</h1>
          <span class="right">
            <span class="badge">관리자</span>
            <span>{{ session?.name }} / {{ session?.department }}</span>
          </span>
        </div>
        <nav class="tabs admin-tabs" :aria-label="`${group.label} 화면`">
          <RouterLink
            v-for="t in group.tabs"
            :key="t.name"
            :to="{ name: t.name }"
            class="tab"
            :class="{ on: route.name === t.name }"
            :aria-current="route.name === t.name ? 'page' : undefined"
          >
            {{ t.label }}
          </RouterLink>
        </nav>
      </header>
      <p v-if="logoutError" class="field-error" role="alert">{{ logoutError }}</p>
      <RouterView v-if="connected" />
      <main v-else class="page-body">
        <section class="panel panel-pad">
          <h2>{{ route.meta.title }} 연결 준비 중</h2>
          <p>이 메뉴는 아직 연결되지 않았습니다. 회원 목록 조회와 가입 승인은 회원 관리에서 사용할 수 있습니다.</p>
          <RouterLink :to="{ name: 'admin-members' }" class="btn btn-primary">회원 관리로 이동</RouterLink>
        </section>
      </main>
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
.admin-header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.admin-header .page-header {
  border-bottom: 0;
  padding-bottom: 6px;
}
.admin-tabs {
  padding: 0 24px;
  border-bottom: 0;
}
.admin-tabs .tab {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
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
