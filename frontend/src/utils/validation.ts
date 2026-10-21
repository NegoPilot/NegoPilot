const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function isEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim())
}

/** 영문·숫자·특수문자를 모두 포함한 8자 이상. 맞으면 null, 아니면 안내 문구 */
export function passwordProblem(value: string): string | null {
  if (value.length < 8) return '8자 이상 입력해 주세요.'
  if (!/[A-Za-z]/.test(value) || !/\d/.test(value) || !/[^A-Za-z0-9]/.test(value)) {
    return '영문, 숫자, 특수문자를 모두 넣어 주세요.'
  }
  return null
}

export function isVerificationCode(value: string): boolean {
  return /^\d{6}$/.test(value.trim())
}
