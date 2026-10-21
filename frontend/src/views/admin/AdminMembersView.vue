<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  CURRENT_ADMIN,
  DEFAULT_SIGNUP_ROLE_LABEL,
  type Member,
  type MemberRole,
  type MemberStatus,
  ROLE_LABEL,
  STATUS_LABEL,
} from '@/data/accounts'
import { useAdminStore } from '@/stores/admin'

const route = useRoute()
const router = useRouter()
const admin = useAdminStore()
const { state } = admin

const message = ref<string | null>(null)

// ── 가입 승인 대기 ──
const requestedEmail = computed(() => {
  const v = route.query.request
  return typeof v === 'string' ? v : null
})
const requestIndex = computed(() => {
  const i = state.requests.findIndex((r) => r.email === requestedEmail.value)
  return i >= 0 ? i : 0
})
const request = computed(() => state.requests[requestIndex.value] ?? null)
const approveRole = ref<MemberRole>('staff')
watch(request, () => (approveRole.value = 'staff'))

function showRequest(offset: number) {
  const next = state.requests[requestIndex.value + offset]
  if (next) router.replace({ query: { ...route.query, request: next.email } })
}

function clearRequestQuery() {
  const query = { ...route.query }
  delete query.request
  router.replace({ query })
}

function approve() {
  const r = request.value
  if (!r) return
  admin.approve(r.email, approveRole.value)
  message.value = `${r.name} 님을 ${approveRole.value === 'lead' ? '구매팀장' : DEFAULT_SIGNUP_ROLE_LABEL}(으)로 승인했습니다.`
  clearRequestQuery()
}

function reject() {
  const r = request.value
  if (!r || !window.confirm(`${r.name} 님의 가입 신청을 반려할까요?`)) return
  admin.reject(r.email)
  message.value = `${r.name} 님의 가입 신청을 반려했습니다.`
  clearRequestQuery()
}

// ── 회원 목록 ──
const PAGE_SIZE = 4
const draft = reactive({ keyword: '', role: '' as MemberRole | '', status: '' as MemberStatus | '' })
const applied = reactive({ ...draft })
const page = ref(1)

function search() {
  Object.assign(applied, draft)
  page.value = 1
}

const filtered = computed(() => {
  const keyword = applied.keyword.trim().toLowerCase()
  return state.members.filter(
    (m) =>
      (!keyword || m.name.toLowerCase().includes(keyword) || m.email.toLowerCase().includes(keyword)) &&
      (!applied.role || m.role === applied.role) &&
      (!applied.status || m.status === applied.status),
  )
})
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
watch(pageCount, (n) => {
  if (page.value > n) page.value = n
})
const rows = computed(() => filtered.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
const rangeText = computed(() => {
  if (!filtered.value.length) return '조건에 맞는 회원이 없습니다'
  const start = (page.value - 1) * PAGE_SIZE + 1
  const end = Math.min(page.value * PAGE_SIZE, filtered.value.length)
  return `전체 ${filtered.value.length}명 중 ${start}–${end}명`
})

/** 1 2 3 … 6 처럼 앞쪽·현재 주변·마지막 페이지만 보여 준다 */
const pageItems = computed<(number | '…')[]>(() => {
  const n = pageCount.value
  const keep = new Set([1, 2, 3, page.value - 1, page.value, page.value + 1, n].filter((p) => p >= 1 && p <= n))
  const sorted = [...keep].sort((a, b) => a - b)
  const items: (number | '…')[] = []
  sorted.forEach((p, i) => {
    const prev = sorted[i - 1]
    if (prev !== undefined && p - prev > 1) items.push('…')
    items.push(p)
  })
  return items
})

type MemberAction = 'lead' | 'staff' | 'suspend'
function actionsFor(m: Member): { value: MemberAction; label: string }[] {
  const list: { value: MemberAction; label: string }[] = []
  if (m.role !== 'lead') list.push({ value: 'lead', label: '팀장으로 변경' })
  if (m.role !== 'staff') list.push({ value: 'staff', label: '실무자로 변경' })
  list.push({ value: 'suspend', label: '이용 정지' })
  return list
}

function onAction(m: Member, event: Event) {
  const select = event.target as HTMLSelectElement
  const action = select.value as MemberAction | ''
  select.value = ''
  if (!action) return
  if (action === 'suspend') {
    if (!window.confirm(`${m.name} 님의 계정 이용을 정지할까요?`)) return
    admin.setSuspended(m.email, true)
    message.value = `${m.name} 님의 계정 이용을 정지했습니다.`
  } else {
    admin.changeRole(m.email, action)
    message.value = `${m.name} 님의 역할을 ${ROLE_LABEL[action]}(으)로 바꿨습니다.`
  }
}

function resume(m: Member) {
  admin.setSuspended(m.email, false)
  message.value = `${m.name} 님의 계정 이용을 재개했습니다.`
}

function invite() {
  message.value = '회원 초대 메일은 회원 API가 연결되면 보낼 수 있습니다.'
}
</script>

<template>
  <main class="page-body">
    <p class="intro">가입 요청을 처리하고 회원의 역할과 이용 상태를 관리합니다.</p>
    <p v-if="message" class="message" role="status">{{ message }}</p>

    <section class="panel panel-pad" aria-labelledby="pending-h">
      <div class="panel-title">
        <h2 id="pending-h">가입 승인 대기</h2>
        <span class="pager-mini">
          <template v-if="state.requests.length > 1">
            <button class="link-btn" type="button" :disabled="requestIndex === 0" @click="showRequest(-1)">← 이전</button>
            <span class="muted">{{ requestIndex + 1 }} / {{ state.requests.length }}</span>
            <button class="link-btn" type="button" :disabled="requestIndex === state.requests.length - 1" @click="showRequest(1)">다음 →</button>
          </template>
          <span class="badge badge-warning">승인 대기</span>
        </span>
      </div>

      <div v-if="request" class="request">
        <div class="who">
          <strong>{{ request.name }} · {{ request.department }}</strong>
          <span>{{ request.email }} / {{ request.company }}</span>
          <span class="muted">신청일 {{ request.requestedAt }} · 기본 역할: {{ DEFAULT_SIGNUP_ROLE_LABEL }}</span>
          <span class="footnote">팀장·관리자 권한은 가입 승인 후 별도 변경합니다.</span>
        </div>
        <div class="decide">
          <label class="role-pick">
            승인 역할:
            <select v-model="approveRole">
              <option value="staff">{{ DEFAULT_SIGNUP_ROLE_LABEL }}</option>
              <option value="lead">구매팀장</option>
            </select>
          </label>
          <div class="decide-btns">
            <button class="btn" type="button" @click="reject">반려</button>
            <button class="btn btn-primary" type="button" @click="approve">가입 승인</button>
          </div>
        </div>
      </div>
      <p v-else class="muted">대기 중인 가입 신청이 없습니다.</p>
    </section>

    <section class="panel panel-pad" aria-labelledby="members-h">
      <div class="panel-title">
        <h2 id="members-h">회원 목록 {{ state.members.length }}명</h2>
        <button class="btn invite" type="button" @click="invite">회원 초대</button>
      </div>

      <form class="filters" role="search" @submit.prevent="search">
        <div class="field keyword">
          <label for="m-keyword">회원 검색</label>
          <input id="m-keyword" v-model="draft.keyword" type="search" placeholder="이름 또는 이메일" />
        </div>
        <div class="field">
          <label for="m-role">역할</label>
          <select id="m-role" v-model="draft.role">
            <option value="">전체</option>
            <option v-for="(label, key) in ROLE_LABEL" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <div class="field">
          <label for="m-status">상태</label>
          <select id="m-status" v-model="draft.status">
            <option value="">전체</option>
            <option v-for="(label, key) in STATUS_LABEL" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <button class="btn btn-primary" type="submit">검색</button>
      </form>

      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>이름</th><th>회사 이메일</th><th>부서</th><th>역할</th><th>상태</th><th>최근 접속</th><th>관리</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in rows" :key="m.email">
              <td>{{ m.name }}</td>
              <td>{{ m.email }}</td>
              <td>{{ m.department }}</td>
              <td>{{ ROLE_LABEL[m.role] }}</td>
              <td :class="{ suspended: m.status === 'suspended' }">{{ STATUS_LABEL[m.status] }}</td>
              <td class="num">{{ m.lastAccess }}</td>
              <td>
                <span v-if="m.email === CURRENT_ADMIN.email">본인 계정</span>
                <button v-else-if="m.status === 'suspended'" class="link-btn" type="button" @click="resume(m)">이용 재개</button>
                <select v-else class="action" :aria-label="`${m.name} 권한 변경`" @change="onAction(m, $event)">
                  <option value="">권한 변경</option>
                  <option v-for="a in actionsFor(m)" :key="a.value" :value="a.value">{{ a.label }}</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="list-foot">
        <span class="muted">{{ rangeText }}</span>
        <nav v-if="pageCount > 1" class="pages" aria-label="회원 목록 페이지">
          <button class="link-btn" type="button" :disabled="page === 1" @click="page--">이전</button>
          <template v-for="(p, i) in pageItems" :key="`${p}-${i}`">
            <span v-if="p === '…'" class="muted">…</span>
            <button v-else class="link-btn" :class="{ current: p === page }" type="button" :aria-current="p === page ? 'page' : undefined" @click="page = p">{{ p }}</button>
          </template>
          <button class="link-btn" type="button" :disabled="page === pageCount" @click="page++">다음</button>
        </nav>
      </div>
    </section>

    <p class="footnote">관리자 변경·이용 정지 등의 작업은 처리 사유와 함께 이력에 기록합니다. 화면의 회원 정보는 설계용 예시입니다.</p>
  </main>
</template>

<style scoped>
.intro {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
.message {
  margin: 0;
  padding: 10px 14px;
  font-size: 12px;
  background: var(--primary-soft);
  color: var(--primary);
  border-radius: var(--radius);
}
.pager-mini {
  display: flex;
  align-items: center;
  gap: 10px;
}
.link-btn:disabled {
  color: var(--text-3);
  cursor: not-allowed;
}
.request {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 16px 32px;
}
.who {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
.who strong {
  font-size: 13px;
}
.decide {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: min(100%, 280px);
}
.role-pick {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 600;
}
.role-pick select {
  width: auto;
  height: 28px;
  border: 0;
  padding: 0 4px;
  font-weight: 600;
}
.decide-btns {
  display: grid;
  grid-template-columns: 1fr 1.7fr;
  gap: 8px;
}
.invite {
  min-width: 166px;
}
.filters {
  display: grid;
  grid-template-columns: minmax(180px, 2.2fr) repeat(2, minmax(110px, 1fr)) minmax(110px, 1fr);
  gap: 12px;
  align-items: end;
  margin-bottom: 16px;
}
.filters .field > label {
  font-weight: 600;
  color: var(--text);
}
.suspended {
  color: var(--danger);
}
.action {
  width: auto;
  height: 28px;
  padding: 0 6px;
  border-color: transparent;
  background: transparent;
  font-size: 12px;
}
.action:hover {
  border-color: var(--border-strong);
}
.list-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
}
.pages {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pages .current {
  font-weight: 700;
  text-decoration: underline;
}
.footnote {
  margin: 0;
}
@media (max-width: 760px) {
  .filters {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
