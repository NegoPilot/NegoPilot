import { computed, reactive } from 'vue'

import {
  type AdminLog,
  CURRENT_ADMIN,
  type Member,
  type MemberRole,
  ROLE_LABEL,
  SEED_LOGS,
  SEED_MEMBERS,
  SEED_REQUESTS,
  type SignupRequest,
} from '@/data/accounts'

// 관리자 화면이 함께 쓰는 예시 상태. 새로고침하면 처음 데이터로 돌아간다 (서버에 저장하지 않음)
const state = reactive({
  members: SEED_MEMBERS.map((m) => ({ ...m })) as Member[],
  requests: SEED_REQUESTS.map((r) => ({ ...r })) as SignupRequest[],
  logs: SEED_LOGS.map((l) => ({ ...l })) as AdminLog[],
})

function stamp(date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function log(action: string, target: string) {
  state.logs.unshift({ at: stamp(), actor: CURRENT_ADMIN.name, action, target })
}

const summary = computed(() => ({
  total: state.members.length,
  active: state.members.filter((m) => m.status === 'active').length,
  pending: state.requests.length,
  leads: state.members.filter((m) => m.role === 'lead').length,
  suspended: state.members.filter((m) => m.status === 'suspended').length,
}))

/** 회원가입 화면에서 신청하면 승인 대기 목록 맨 앞에 넣는다. 같은 이메일이면 내용을 바꾼다 */
function submitRequest(request: SignupRequest) {
  const i = state.requests.findIndex((r) => r.email === request.email)
  if (i >= 0) state.requests.splice(i, 1)
  state.requests.unshift(request)
}

function approve(email: string, role: MemberRole) {
  const i = state.requests.findIndex((r) => r.email === email)
  const r = state.requests[i]
  if (!r) return
  state.requests.splice(i, 1)
  state.members.unshift({
    name: r.name,
    email: r.email,
    department: r.department,
    role,
    status: 'active',
    lastAccess: '-',
  })
  log(`가입 승인 · ${role === 'staff' ? '구매 실무자' : `구매${ROLE_LABEL[role]}`}`, `${r.name} / ${r.department}`)
}

function reject(email: string) {
  const i = state.requests.findIndex((r) => r.email === email)
  const r = state.requests[i]
  if (!r) return
  state.requests.splice(i, 1)
  log('가입 반려', `${r.name} / ${r.department}`)
}

function changeRole(email: string, role: MemberRole) {
  const m = state.members.find((x) => x.email === email)
  if (!m || m.role === role) return
  log(`회원 역할 변경 · ${ROLE_LABEL[m.role]} → ${ROLE_LABEL[role]}`, `${m.name} / ${m.department}`)
  m.role = role
}

function setSuspended(email: string, suspended: boolean) {
  const m = state.members.find((x) => x.email === email)
  if (!m) return
  m.status = suspended ? 'suspended' : 'active'
  log(suspended ? '계정 이용 정지' : '계정 이용 재개', `${m.name} / ${m.department}`)
}

export function useAdminStore() {
  return { state, summary, submitRequest, approve, reject, changeRole, setSuspended }
}
