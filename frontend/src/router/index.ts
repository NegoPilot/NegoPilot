import { createRouter, createWebHistory } from 'vue-router'

import { ensureSession, session } from '@/stores/session'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    /** 사이드바 없이 단독으로 그리는 화면 (새 탭) */
    standalone?: boolean
    /** public: 로그인 전 화면(상단 띠+푸터), admin: 관리자 사이드바. 없으면 업무 화면 사이드바 */
    layout?: 'public' | 'admin'
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: { name: 'chat' } },
    { path: '/chat/:conversationId?', name: 'chat', component: () => import('@/views/ChatView.vue'), meta: { title: 'AI 대화' } },
    {
      path: '/overview',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
      meta: { title: '홈' },
    },
    {
      // 검색 조건·선택 협력사·AI 패널 여부는 query에 둔다
      path: '/suppliers',
      name: 'suppliers',
      component: () => import('@/views/SupplierWorkspaceView.vue'),
      meta: { title: '협력사' },
    },
    {
      path: '/suppliers/:supplierId',
      redirect: (to) => ({ name: 'suppliers', query: { selected: String(to.params.supplierId) } }),
    },
    {
      path: '/review',
      name: 'law-review',
      component: () => import('@/views/LawReviewView.vue'),
      meta: { title: '법규 검토' },
    },
    {
      // 새 탭에서 열리므로 URL만으로 대화를 다시 불러온다
      path: '/evidence/:conversationId/:responseId/:evidenceId',
      name: 'evidence-detail',
      component: () => import('@/views/EvidenceDetailView.vue'),
      props: true,
      meta: { title: '근거 자료 상세', standalone: true },
    },
    // ── 로그인 전 (Figma 07~09) ──
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/auth/LoginView.vue'),
      meta: { title: '로그인', layout: 'public' },
    },
    {
      path: '/signup',
      name: 'signup',
      component: () => import('@/views/auth/SignupView.vue'),
      meta: { title: '회원가입', layout: 'public' },
    },
    {
      path: '/signup/pending',
      name: 'signup-pending',
      component: () => import('@/views/auth/SignupPendingView.vue'),
      meta: { title: '가입 승인 대기', layout: 'public' },
    },
    // ── 관리자 (Figma 10~11) ──
    {
      path: '/admin',
      name: 'admin',
      component: () => import('@/views/admin/AdminDashboardView.vue'),
      meta: { title: '관리 현황', layout: 'admin' },
    },
    {
      // 승인할 신청은 ?request=이메일 로 고른다
      path: '/admin/members',
      name: 'admin-members',
      component: () => import('@/views/admin/AdminMembersView.vue'),
      meta: { title: '회원·권한 관리', layout: 'admin' },
    },
    {
      path: '/admin/approvals',
      name: 'admin-approvals',
      component: () => import('@/views/admin/AdminApprovalsView.vue'),
      meta: { title: '가입 승인', layout: 'admin' },
    },
    {
      path: '/admin/history',
      name: 'admin-history',
      component: () => import('@/views/admin/AdminHistoryView.vue'),
      meta: { title: '접근 이력', layout: 'admin' },
    },
    // ── 데이터 관리 (Figma 12~14). 등록 API 연결 전 ──
    {
      path: '/admin/data',
      name: 'admin-data',
      component: () => import('@/views/admin/DataManagementView.vue'),
      meta: { title: '데이터 관리', layout: 'admin' },
    },
    {
      path: '/admin/data/suppliers/new',
      name: 'admin-supplier-new',
      component: () => import('@/views/admin/SupplierRegisterView.vue'),
      meta: { title: '협력사 등록', layout: 'admin' },
    },
    {
      path: '/admin/data/laws/new',
      name: 'admin-law-new',
      component: () => import('@/views/admin/LawRegisterView.vue'),
      meta: { title: '법령 등록', layout: 'admin' },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: '페이지 없음', layout: 'public' },
    },
  ],
})

router.beforeEach(async (to) => {
  await ensureSession()
  if (to.meta.layout === 'public') {
    if (session.value?.status === 'ACTIVE' && ['login', 'signup', 'signup-pending'].includes(String(to.name))) return { name: 'chat' }
    if (session.value?.status === 'PENDING' && to.name !== 'signup-pending') return { name: 'signup-pending' }
    return true
  }
  if (!session.value) return { name: 'login', query: { redirect: to.fullPath } }
  if (session.value.status === 'PENDING') return { name: 'signup-pending' }
  if (to.meta.layout === 'admin' && session.value.role !== 'ADMIN') return { name: 'chat' }
  return true
})

window.addEventListener('negopilot:unauthenticated', () => {
  if (router.currentRoute.value.meta.layout !== 'public') {
    void router.replace({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · NegoPilot` : 'NegoPilot'
})
