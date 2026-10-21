import type { ApiErrorBody } from './types'

// Spring 응답 제한 시간(35초)보다 조금 길게 둔다
const DEFAULT_TIMEOUT_MS = 40_000

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, retryable: boolean, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.retryable = retryable
    this.requestId = requestId
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false
  const error = (value as { error: unknown }).error
  return typeof error === 'object' && error !== null && typeof (error as { code?: unknown }).code === 'string'
}

export async function request<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (init.body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(path, { ...init, headers, signal: controller.signal })
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError(0, 'CLIENT_TIMEOUT', 'Request timed out.', true)
    }
    throw new ApiError(0, 'NETWORK_ERROR', 'Network request failed.', true)
  } finally {
    clearTimeout(timer)
  }

  const text = await response.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }

  if (!response.ok) {
    if (isErrorBody(body)) {
      throw new ApiError(response.status, body.error.code, body.error.message, body.error.retryable, body.request_id)
    }
    // Spring이 꺼져 있으면 Vite 프록시가 본문 없는 5xx를 돌려준다
    if (response.status >= 500) {
      throw new ApiError(response.status, 'BACKEND_UNREACHABLE', 'Backend did not respond.', true)
    }
    throw new ApiError(response.status, `HTTP_${response.status}`, 'Unexpected response.', false)
  }
  if (body === null) {
    throw new ApiError(response.status, 'EMPTY_RESPONSE', 'Response body was empty.', false)
  }
  return body as T
}
