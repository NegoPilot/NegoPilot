// 가입 신청 내용. 승인 대기 화면과 '신청 정보 수정하기'에서 다시 쓴다.
// 비밀번호는 저장하지 않는다. 이 탭에서만 남고(sessionStorage) 저장소를 못 쓰면 메모리에만 둔다.

export interface SignupDraft {
  name: string
  department: string
  companyCode: string
  position: string
  email: string
}

const KEY = 'negopilot.signupDraft'
let memory: SignupDraft | null = null

export function saveSignupDraft(draft: SignupDraft) {
  memory = { ...draft }
  try {
    sessionStorage.setItem(KEY, JSON.stringify(memory))
  } catch {
    // 저장소를 쓸 수 없는 환경(사생활 보호 모드 등)이면 메모리 값만 쓴다
  }
}

export function loadSignupDraft(): SignupDraft | null {
  if (memory) return { ...memory }
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (typeof value === 'object' && value !== null && typeof (value as SignupDraft).email === 'string') {
      memory = value as SignupDraft
      return { ...memory }
    }
  } catch {
    return null
  }
  return null
}
