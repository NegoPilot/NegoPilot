import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/suppliers' },
    {
      path: '/suppliers',
      name: 'supplier-search',
      component: () => import('@/views/SupplierSearchView.vue'),
      meta: { title: '협력사 탐색' },
    },
    {
      path: '/suppliers/:supplierId',
      name: 'supplier-detail',
      component: () => import('@/views/SupplierDetailView.vue'),
      props: true,
      meta: { title: '협력사 상세' },
    },
    {
      path: '/chat',
      name: 'chat-empty',
      component: () => import('@/views/ChatView.vue'),
      meta: { title: '협상 도우미' },
    },
    {
      path: '/chat/:conversationId',
      name: 'chat',
      component: () => import('@/views/ChatView.vue'),
      props: true,
      meta: { title: '협상 도우미' },
    },
    {
      // 새 탭에서 열리므로 URL만으로 대화를 다시 불러온다
      path: '/evidence/:conversationId/:responseId/:evidenceId',
      name: 'evidence-detail',
      component: () => import('@/views/EvidenceDetailView.vue'),
      props: true,
      meta: { title: '근거 자료 상세' },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: '페이지 없음' },
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} · NegoPilot` : 'NegoPilot'
})
