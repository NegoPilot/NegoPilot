import { ApiError } from '@/api/client'

// 서버의 error.message는 영문 고정 문구라 화면에는 코드별 문장을 보여 준다
const MESSAGES: Record<string, string> = {
  VALIDATION_ERROR: '입력값을 확인해 주세요.',
  SUPPLIER_NOT_FOUND: '협력사를 찾을 수 없습니다.',
  CONVERSATION_NOT_FOUND: '대화를 찾을 수 없거나 접근 권한이 없습니다.',
  RESPONSE_NOT_FOUND: '저장된 답변을 찾을 수 없습니다.',
  CONVERSATION_BUSY: '이 대화의 이전 질문을 처리하고 있습니다. 잠시 뒤 다시 시도해 주세요.',
  CONVERSATION_LEASE_EXPIRED: '답변 저장 시간이 만료되었습니다. 대화를 다시 확인한 뒤 재시도해 주세요.',
  IDEMPOTENCY_CONFLICT: '이 요청 키로 다른 질문을 보낼 수 없습니다. 새 질문으로 다시 보내 주세요.',
  UNAUTHENTICATED: '로그인이 필요합니다.',
  INVALID_CREDENTIALS: '이메일과 비밀번호를 확인해 주세요.',
  EMAIL_ALREADY_REGISTERED: '이미 가입한 이메일입니다. 로그인해 주세요.',
  FORBIDDEN: '승인된 계정 또는 필요한 권한이 없습니다.',
  ACCOUNT_NOT_ACTIVE: '계정 상태가 변경되었습니다. 다시 로그인해 주세요.',
  AUTH_UNAVAILABLE: '인증 서버에 연결할 수 없습니다.',
  CSRF_INVALID: '세션 확인이 필요합니다. 다시 시도해 주세요.',
  INVALID_RESPONSE: '서버 응답 형식을 확인할 수 없습니다.',
  GENERATION_FAILED: '답변을 생성하지 못했습니다. 질문을 확인하고 다시 시도해 주세요.',
  DECOMPOSITION_FAILED: '질문을 해석하지 못했습니다. 조건을 조금 더 설명해 주세요.',
  DEMO_MODE_REQUIRED: '테스트 모드(stub)에서만 쓸 수 있는 기능입니다. 서버 설정을 확인해 주세요.',
  UPSTREAM_UNAVAILABLE: 'AI 서버 또는 모델을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.',
  UPSTREAM_TIMEOUT: '응답 시간이 초과됐습니다.',
  UPSTREAM_INVALID_RESPONSE: 'Agent 서버의 응답이 올바르지 않습니다.',
  INTERNAL_ERROR: '서버에서 오류가 났습니다.',
  BACKEND_UNREACHABLE: '백엔드 서버에 연결할 수 없습니다. Spring이 실행 중인지 확인해 주세요.',
  NETWORK_ERROR: '네트워크에 연결할 수 없습니다.',
  CLIENT_TIMEOUT: '응답 시간이 초과됐습니다.',
  EMPTY_RESPONSE: '서버 응답이 비어 있습니다.',
  MEMBER_NOT_FOUND: '회원을 찾을 수 없습니다. 목록을 새로 확인해 주세요.',
  SELF_CHANGE_FORBIDDEN: '본인의 역할이나 계정 상태는 변경할 수 없습니다.',
  INVALID_STATUS_TRANSITION: '현재 상태에서는 처리할 수 없습니다. 목록을 새로 확인해 주세요.',
  MEMBER_NOT_ACTIVE: '활성 상태인 회원만 역할을 변경할 수 있습니다.',
  LAST_ADMIN_REQUIRED: '활성 관리자 계정이 최소 한 개 필요합니다.',
}

export interface ErrorView {
  message: string
  retryable: boolean
  code: string
}

export function describeError(error: unknown): ErrorView {
  if (error instanceof ApiError) {
    return {
      message: MESSAGES[error.code] ?? '요청을 처리하지 못했습니다.',
      retryable: error.retryable,
      code: error.code,
    }
  }
  return { message: '알 수 없는 오류가 났습니다.', retryable: false, code: 'UNKNOWN' }
}

export function isApiError(error: unknown, code: string): boolean {
  return error instanceof ApiError && error.code === code
}
