import { ApiError } from '@/api/client'

// 서버의 error.message는 영문 고정 문구라 화면에는 코드별 문장을 보여 준다
const MESSAGES: Record<string, string> = {
  VALIDATION_ERROR: '입력값을 확인해 주세요.',
  SUPPLIER_NOT_FOUND: '협력사를 찾을 수 없습니다.',
  CONVERSATION_NOT_FOUND: '대화를 찾을 수 없습니다. 서버를 다시 시작하면 대화 기록이 사라집니다.',
  DEMO_MODE_REQUIRED: '테스트 모드(stub)에서만 쓸 수 있는 기능입니다. 서버 설정을 확인해 주세요.',
  UPSTREAM_UNAVAILABLE: 'Agent 서버에 연결할 수 없습니다.',
  UPSTREAM_TIMEOUT: '응답 시간이 초과됐습니다.',
  UPSTREAM_INVALID_RESPONSE: 'Agent 서버의 응답이 올바르지 않습니다.',
  INTERNAL_ERROR: '서버에서 오류가 났습니다.',
  BACKEND_UNREACHABLE: '백엔드 서버에 연결할 수 없습니다. Spring이 실행 중인지 확인해 주세요.',
  NETWORK_ERROR: '네트워크에 연결할 수 없습니다.',
  CLIENT_TIMEOUT: '응답 시간이 초과됐습니다.',
  EMPTY_RESPONSE: '서버 응답이 비어 있습니다.',
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
