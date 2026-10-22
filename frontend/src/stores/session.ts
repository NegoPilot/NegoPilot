import { readonly, ref } from 'vue'
import { currentMember, login, logout, type Member } from '@/api/auth'
import { ApiError } from '@/api/client'
import { resetAssistant } from '@/stores/assistant'

const current = ref<Member | null>(null)
const failure = ref<unknown>(null)
let initialized = false
let pending: Promise<Member | null> | null = null
// 이 탭에서 로그인·로그아웃하면 늘린다. 그 전에 보낸 회원 확인 응답은 버린다.
let changes = 0
export const session = readonly(current)
export const sessionError = readonly(failure)
// 이전 시안의 localStorage 역할 값은 인증에 사용하지 않는다.
try { localStorage.removeItem('negopilot.session') } catch { /* 저장소 접근 없이도 세션을 확인한다. */ }
window.addEventListener('negopilot:unauthenticated', () => { changes += 1; setMember(null); initialized = false })

// 이전 회원의 AI 대화를 다음 회원이 이어 열지 않도록 회원이 바뀌면 비운다.
function setMember(user: Member | null) {
  if (current.value && current.value.id !== user?.id) resetAssistant()
  current.value = user
}
function memberKey(user: Member | null) { return user ? `${user.id}:${user.role}:${user.status}` : null }

// 세션 쿠키는 탭끼리 같이 쓰므로 다른 탭의 로그인·로그아웃을 알리고 받는다.
let channel: BroadcastChannel | null = null
try {
  channel = new BroadcastChannel('negopilot.session')
  channel.onmessage = () => { void syncSession() }
} catch { /* 알림 없이도 탭을 다시 볼 때 확인한다. */ }
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void syncSession() })
function announce() { channel?.postMessage('changed') }

/** 다른 탭에서 계정이 바뀌었는지 확인한다. 일시적인 오류로는 회원을 비우지 않는다. */
async function syncSession() {
  if (!initialized || pending) return
  const token = changes
  let user: Member | null
  try { user = await currentMember() }
  catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) return
    user = null
  }
  if (token !== changes) return
  const previous = memberKey(current.value)
  setMember(user)
  if (memberKey(user) !== previous) window.dispatchEvent(new Event('negopilot:member-changed'))
}

export async function refreshSession(): Promise<Member | null> {
  if (pending) return pending
  pending = (async () => {
    try { setMember(await currentMember()); failure.value = null }
    catch (error) {
      setMember(null)
      failure.value = error instanceof ApiError && error.status === 401 ? null : error
    }
    initialized = true
    return current.value
  })().finally(() => { pending = null })
  return pending
}
export function ensureSession() { return initialized ? Promise.resolve(current.value) : refreshSession() }
export async function signIn(email: string, password: string) {
  const user = await login(email, password)
  changes += 1
  setMember(user)
  failure.value = null
  initialized = true
  announce()
  return user
}
export async function signOut() {
  try { await logout() }
  catch (error) { if (!(error instanceof ApiError && error.status === 401)) throw error }
  changes += 1
  setMember(null)
  initialized = true
  announce()
}
export function safeRedirect(value: unknown): string | null {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') ? value : null
}
