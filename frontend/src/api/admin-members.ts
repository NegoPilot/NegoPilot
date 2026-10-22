import { API_PATHS, ApiError, request } from './client'

export type AdminMemberRole = 'USER' | 'ADMIN'
export type AdminMemberStatus = 'PENDING' | 'ACTIVE' | 'REJECTED' | 'DISABLED'

export interface AdminMember {
  id: string
  email: string
  name: string
  department: string
  job_title: string
  role: AdminMemberRole
  status: AdminMemberStatus
  created_at: string
  updated_at: string
}

export interface MemberListParams {
  search?: string
  role?: AdminMemberRole
  status?: AdminMemberStatus
  /** 서버 페이지는 0부터 시작한다. */
  page?: number
  size?: number
}

export interface MemberPage {
  items: AdminMember[]
  total: number
  page: number
  size: number
}

export const MEMBER_ROLE_LABEL: Record<AdminMemberRole, string> = {
  USER: '일반 회원',
  ADMIN: '관리자',
}
export const MEMBER_STATUS_LABEL: Record<AdminMemberStatus, string> = {
  PENDING: '승인 대기',
  ACTIVE: '활성',
  REJECTED: '반려',
  DISABLED: '비활성',
}

const ROOT = `${API_PATHS.admin}/members`
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
const ROLES: readonly string[] = ['USER', 'ADMIN']
const STATUSES: readonly string[] = ['PENDING', 'ACTIVE', 'REJECTED', 'DISABLED']
const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

export function formatMemberDate(value: string): string {
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? dateFormatter.format(timestamp) : '확인 불가'
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function integer(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max
}
function timestamp(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}
function invalidResponse(): never {
  throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid member response.', false)
}
function invalidRequest(): never {
  throw new ApiError(400, 'VALIDATION_ERROR', 'Invalid member request.', false)
}
function parseMember(value: unknown): AdminMember {
  if (!object(value) || typeof value.id !== 'string' || !UUID.test(value.id)
    || typeof value.email !== 'string' || typeof value.name !== 'string'
    || typeof value.department !== 'string' || typeof value.job_title !== 'string'
    || typeof value.role !== 'string' || !ROLES.includes(value.role)
    || typeof value.status !== 'string' || !STATUSES.includes(value.status)
    || !timestamp(value.created_at) || !timestamp(value.updated_at)) invalidResponse()
  // 알려진 공개 필드만 넘겨 회원 목록에 의도하지 않은 응답 필드가 섞이지 않게 한다.
  return {
    id: value.id, email: value.email, name: value.name, department: value.department,
    job_title: value.job_title, role: value.role as AdminMemberRole, status: value.status as AdminMemberStatus,
    created_at: value.created_at, updated_at: value.updated_at,
  }
}

export async function listMembers(options: MemberListParams = {}, signal?: AbortSignal): Promise<MemberPage> {
  const page = options.page ?? 0
  const size = options.size ?? 20
  if (!integer(page, 0, 100_000) || !integer(size, 1, 100)
    || (options.search !== undefined && (typeof options.search !== 'string' || options.search.length > 254 || options.search.includes('\0')))
    || (options.role !== undefined && !ROLES.includes(options.role))
    || (options.status !== undefined && !STATUSES.includes(options.status))) invalidRequest()
  const query = new URLSearchParams({ page: String(page), size: String(size) })
  const search = options.search?.trim()
  if (search) query.set('search', search)
  if (options.role !== undefined) query.set('role', options.role)
  if (options.status !== undefined) query.set('status', options.status)
  const response = await request<unknown>(`${ROOT}?${query}`, { signal })
  if (!object(response) || !Array.isArray(response.items)
    || !integer(response.total, 0, Number.MAX_SAFE_INTEGER)
    || !integer(response.page, 0, 100_000) || response.page !== page
    || !integer(response.size, 1, 100) || response.size !== size
    || response.items.length > response.size) invalidResponse()
  const items = response.items.map(parseMember)
  if (new Set(items.map((item) => item.id.toLowerCase())).size !== items.length) invalidResponse()
  return { items, total: response.total, page: response.page, size: response.size }
}

export async function updateMemberStatus(id: string, status: AdminMemberStatus): Promise<AdminMember> {
  if (typeof id !== 'string' || !UUID.test(id) || !STATUSES.includes(status)) invalidRequest()
  const response = await request<unknown>(`${ROOT}/${encodeURIComponent(id)}/status`, {
    method: 'PATCH', body: JSON.stringify({ status }),
  })
  if (!object(response)) invalidResponse()
  const member = parseMember(response.user)
  if (member.id.toLowerCase() !== id.toLowerCase() || member.status !== status) invalidResponse()
  return member
}
