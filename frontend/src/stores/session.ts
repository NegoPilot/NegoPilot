import { readonly, ref } from 'vue'

// 예시 로그인 상태. 인증 API가 없어서 서버 확인 없이 이 브라우저에만 남긴다 (보안 기능이 아님).
// 근거 자료 상세는 새 탭(noopener)으로 열리므로 탭끼리 공유되는 localStorage에 둔다.

export interface Session {
  email: string
  role: 'member' | 'admin'
}

const KEY = 'negopilot.session'

function read(): Session | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    const s = value as Session
    if (typeof s?.email === 'string' && (s.role === 'member' || s.role === 'admin')) return s
  } catch {
    // 저장소를 못 쓰면 로그인하지 않은 상태로 본다
  }
  return null
}

const current = ref<Session | null>(read())

export const session = readonly(current)

export function signIn(email: string, role: Session['role']) {
  current.value = { email, role }
  try {
    localStorage.setItem(KEY, JSON.stringify(current.value))
  } catch {
    // 저장하지 못해도 이 탭에서는 로그인 상태로 둔다
  }
}

export function signOut() {
  current.value = null
  try {
    localStorage.removeItem(KEY)
  } catch {
    // 무시
  }
}

/** 로그인 뒤 돌아갈 주소. 같은 사이트 안 경로만 받는다 */
export function safeRedirect(value: unknown): string | null {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : null
}
