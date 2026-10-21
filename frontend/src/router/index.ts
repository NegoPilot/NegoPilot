import { createRouter, createWebHistory } from 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    /** 사이드바 없이 단독으로 그리는 화면 (새 탭) */
    standalone?: boolean
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
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
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: '페이지 없음' },
    },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · NegoPilot` : 'NegoPilot'
})
