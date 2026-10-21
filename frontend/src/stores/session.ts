import { readonly, ref } from 'vue'
import { currentMember, login, logout, type Member } from '@/api/auth'
import { ApiError } from '@/api/client'

const current = ref<Member | null>(null)
const failure = ref<unknown>(null)
let initialized = false
let pending: Promise<Member | null> | null = null
export const session = readonly(current)
export const sessionError = readonly(failure)
// 이전 시안의 localStorage 역할 값은 인증에 사용하지 않는다.
try { localStorage.removeItem('negopilot.session') } catch { /* 저장소 접근 없이도 세션을 확인한다. */ }
window.addEventListener('negopilot:unauthenticated', () => { current.value = null; initialized = false })

export async function refreshSession(): Promise<Member | null> {
  if (pending) return pending
  pending = (async () => {
    try { current.value = await currentMember(); failure.value = null }
    catch (error) {
      current.value = null
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
  current.value = user
  failure.value = null
  initialized = true
  return user
}
export async function signOut() {
  try { await logout() }
  catch (error) { if (!(error instanceof ApiError && error.status === 401)) throw error }
  current.value = null
  initialized = true
}
export function safeRedirect(value: unknown): string | null {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') ? value : null
}
