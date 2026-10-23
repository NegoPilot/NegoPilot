import type { ApiErrorBody } from './types'

const configuredTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS)
export const DEFAULT_TIMEOUT_MS = Number.isFinite(configuredTimeout) && configuredTimeout >= 1_000 && configuredTimeout <= 2_147_483_647 ? configuredTimeout : 40_000

// 공개 API 경로는 여기서만 정한다. URL에는 버전을 두지 않고 응답의 schema_version으로 계약을 구분한다.
const API_ROOT = '/api'
export const API_PATHS = {
  auth: `${API_ROOT}/auth`,
  admin: `${API_ROOT}/admin`,
  conversations: `${API_ROOT}/conversations`,
  businessCatalog: `${API_ROOT}/business/catalog`,
  suppliers: `${API_ROOT}/suppliers`,
  negotiations: `${API_ROOT}/negotiations`,
  negotiationExports: `${API_ROOT}/negotiation-exports`,
} as const

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly requestId: string | null = null,
    readonly details: Record<string, unknown> | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false
  const error = value.error
  return typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string'
    && 'message' in error && typeof error.message === 'string' && 'retryable' in error && typeof error.retryable === 'boolean'
}

export async function request<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  const method = (init.method ?? 'GET').toUpperCase()
  // 로그인 시 토큰이 바뀌므로 각 변경 요청 직전에 현재 세션의 토큰을 받는다.
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = await request<{ header_name: string; token: string }>(`${API_PATHS.auth}/csrf`, { signal: init.signal })
    if (!csrf || typeof csrf.header_name !== 'string' || !/^[A-Za-z0-9-]+$/.test(csrf.header_name)
      || typeof csrf.token !== 'string' || !csrf.token) throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid CSRF response.', false)
    headers.set(csrf.header_name, csrf.token)
  }
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (init.signal?.aborted) abort()
  init.signal?.addEventListener('abort', abort, { once: true })
  const timer = setTimeout(abort, timeoutMs)
  try {
    const response = await fetch(path, { ...init, headers, credentials: 'same-origin', signal: controller.signal })
    const text = await response.text()
    let body: unknown = null
    try { body = text ? JSON.parse(text) : null } catch { /* HTTP 오류를 아래에서 분류한다. */ }
    if (!response.ok) {
      if (isErrorBody(body)) {
        if (body.error.code === 'UNAUTHENTICATED' && path !== `${API_PATHS.auth}/me` && typeof window !== 'undefined') {
          window.dispatchEvent(new Event('negopilot:unauthenticated'))
        }
        const details = (body.error as { details?: unknown }).details
        throw new ApiError(response.status, body.error.code, body.error.message, body.error.retryable, body.request_id ?? null,
          typeof details === 'object' && details !== null && !Array.isArray(details) ? details as Record<string, unknown> : null)
      }
      throw new ApiError(response.status, response.status >= 500 ? 'BACKEND_UNREACHABLE' : `HTTP_${response.status}`, 'Request failed.', response.status >= 500)
    }
    if (response.status === 204) return undefined as T
    if (body === null) throw new ApiError(response.status, 'EMPTY_RESPONSE', 'Response body was empty.', false)
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (init.signal?.aborted) throw new ApiError(0, 'REQUEST_CANCELLED', 'Request was cancelled.', false)
    if (controller.signal.aborted) throw new ApiError(0, 'CLIENT_TIMEOUT', 'Request timed out.', true)
    throw new ApiError(0, 'NETWORK_ERROR', 'Network request failed.', true)
  } finally {
    clearTimeout(timer)
    init.signal?.removeEventListener('abort', abort)
  }
}
