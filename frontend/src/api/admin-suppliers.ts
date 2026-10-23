import { API_PATHS, ApiError, request } from './client'

// 관리자 협력사 관리 API (docs/contracts/admin-suppliers.md).
// 모든 수정은 조회한 version을 보내고, 성공하면 서버가 같은 구조의 상세를 돌려준다.

export type SupplierSource = 'DART' | 'MOCK' | 'MANUAL'
export type SupplierStatus = 'ACTIVE' | 'INACTIVE'
/** 인증·생산능력·품질·견적 값의 출처 */
export type OperatingSource = 'MOCK' | 'MANUAL'

export interface AdminSupplierListItem {
  supplier_id: string
  name: string
  region: string
  source: SupplierSource
  status: SupplierStatus
  version: number
  iatf16949: boolean
  iso14001: boolean
  certification_source: OperatingSource
  capability_source: OperatingSource
  quality_source: OperatingSource
  defect_ppm: number | null
  /** 0..1 비율 */
  on_time_rate: number | null
  capability_count: number
  updated_at: string
}

export interface AdminSupplierPage {
  items: AdminSupplierListItem[]
  total: number
  page: number
  size: number
}

export interface AdminSupplierProfile {
  supplier_id: string
  source: SupplierSource
  name: string
  region: string
  corp_code: string | null
  stock_code: string | null
  induty_code: string | null
  employees: number | null
  revenue_eok: number | null
  founded_year: number
  biz_no_masked: string
  iatf16949: boolean
  iso14001: boolean
  status: SupplierStatus
  version: number
  profile_origin_source: SupplierSource
  certification_source: OperatingSource
  capability_source: OperatingSource
  quality_source: OperatingSource
  created_at: string
  updated_at: string
}

export interface CapabilityInput {
  process: string
  material: string
  monthly_capacity: number
}

export interface QualityInput {
  defect_ppm: number
  /** 0..1 비율, 소수 셋째 자리까지 */
  on_time_rate: number
  audit_grade: string
  years_dealing: number
}

export interface QuoteInput {
  process: string
  material: string
  unit_price: number
  quoted_at: string
}

export interface AdminQuote extends QuoteInput {
  quote_id: number
  source: OperatingSource
}

export interface AdminFinancial {
  bsns_year: number
  fs_div: 'CFS' | 'OFS'
  source: 'DART' | 'MOCK'
  revenue: number | null
  operating_income: number | null
  net_income: number | null
  total_assets: number | null
  total_liabilities: number | null
  total_equity: number | null
}

export interface AdminContract {
  payment_days: number
  price_linkage: boolean
  /** 0..1 비율 */
  raw_material_ratio: number
  auto_renewal: boolean
}

export interface AdminAuditEvent {
  event_id: number
  actor_user_id: string
  action: string
  before_data: unknown
  after_data: unknown
  created_at: string
}

export interface AdminSupplierDetail {
  supplier: AdminSupplierProfile
  capabilities: CapabilityInput[]
  quality: QualityInput | null
  financials: AdminFinancial[]
  contract: AdminContract | null
  quotes: AdminQuote[]
  audit: AdminAuditEvent[]
}

export interface ProfileInput {
  name: string
  region: string
  employees: number | null
  revenue_eok: number | null
  founded_year: number
  biz_no_masked: string
  stock_code: string | null
  induty_code: string | null
}

export interface CreateSupplierRequest {
  /** DART는 corp_code 8자리가 필요하고, 다른 출처는 corp_code를 null로 보낸다 */
  profile: ProfileInput & { source: SupplierSource; corp_code: string | null }
  certifications: { iatf16949: boolean; iso14001: boolean }
  capabilities: CapabilityInput[]
  quality: QualityInput | null
}

export interface SupplierListParams {
  q?: string
  source?: SupplierSource
  status?: SupplierStatus
  /** 서버 페이지는 0부터 시작한다 */
  page?: number
  size?: number
}

export const SOURCE_LABEL: Record<SupplierSource, string> = {
  DART: 'DART 공시',
  MOCK: '가상',
  MANUAL: '관리자 입력',
}
export const STATUS_LABEL: Record<SupplierStatus, string> = {
  ACTIVE: '활성',
  INACTIVE: '비활성',
}
export const AUDIT_ACTION_LABEL: Record<string, string> = {
  CREATE: '등록',
  PROFILE_CERTIFICATIONS: '기본 정보·인증 수정',
  CAPABILITIES: '생산능력 교체',
  QUALITY: '품질 수정',
  QUOTES: '견적 추가',
  STATUS: '상태 변경',
}

const ROOT = `${API_PATHS.admin}/suppliers`
const SUPPLIER_ID = /^SUP-\d{1,18}$/
const SOURCES: readonly string[] = ['DART', 'MOCK', 'MANUAL']
const STATUSES: readonly string[] = ['ACTIVE', 'INACTIVE']

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function integer(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max
}
function invalidResponse(): never {
  throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid supplier response.', false)
}
function invalidRequest(): never {
  throw new ApiError(400, 'VALIDATION_ERROR', 'Invalid supplier request.', false)
}
function supplierPath(id: string, suffix = ''): string {
  if (typeof id !== 'string' || !SUPPLIER_ID.test(id)) invalidRequest()
  return `${ROOT}/${encodeURIComponent(id)}${suffix}`
}
function version(value: number): number {
  if (!integer(value, 0, Number.MAX_SAFE_INTEGER)) invalidRequest()
  return value
}

// 화면이 기대는 식별자·상태·version과 배열 구조만 확인한다. 나머지 값은 계약의 형식대로 넘긴다.
function parseListItem(value: unknown): AdminSupplierListItem {
  if (!object(value) || typeof value.supplier_id !== 'string' || typeof value.name !== 'string'
    || typeof value.source !== 'string' || !SOURCES.includes(value.source)
    || typeof value.status !== 'string' || !STATUSES.includes(value.status)
    || !integer(value.version, 0, Number.MAX_SAFE_INTEGER)
    || !integer(value.capability_count, 0, Number.MAX_SAFE_INTEGER)) invalidResponse()
  return value as unknown as AdminSupplierListItem
}

function parseDetail(value: unknown, id?: string): AdminSupplierDetail {
  if (!object(value) || !object(value.supplier)) invalidResponse()
  const s = value.supplier
  if (typeof s.supplier_id !== 'string' || (id !== undefined && s.supplier_id !== id)
    || typeof s.name !== 'string' || typeof s.source !== 'string' || !SOURCES.includes(s.source)
    || typeof s.status !== 'string' || !STATUSES.includes(s.status)
    || !integer(s.version, 0, Number.MAX_SAFE_INTEGER)
    || !Array.isArray(value.capabilities) || !Array.isArray(value.financials)
    || !Array.isArray(value.quotes) || !Array.isArray(value.audit)
    || (value.quality !== null && !object(value.quality))
    || (value.contract !== null && !object(value.contract))) invalidResponse()
  return value as unknown as AdminSupplierDetail
}

export async function listSuppliers(options: SupplierListParams = {}, signal?: AbortSignal): Promise<AdminSupplierPage> {
  const page = options.page ?? 0
  const size = options.size ?? 20
  const q = options.q?.trim()
  if (!integer(page, 0, 1_000_000) || !integer(size, 1, 100) || (q !== undefined && q.length > 100)
    || (options.source !== undefined && !SOURCES.includes(options.source))
    || (options.status !== undefined && !STATUSES.includes(options.status))) invalidRequest()
  const query = new URLSearchParams({ page: String(page), size: String(size) })
  if (q) query.set('q', q)
  if (options.source !== undefined) query.set('source', options.source)
  if (options.status !== undefined) query.set('status', options.status)
  const response = await request<unknown>(`${ROOT}?${query}`, { signal })
  if (!object(response) || !Array.isArray(response.items)
    || !integer(response.total, 0, Number.MAX_SAFE_INTEGER)
    || response.page !== page || response.size !== size || response.items.length > size) invalidResponse()
  return { items: response.items.map(parseListItem), total: response.total, page, size }
}

export async function getSupplier(id: string, signal?: AbortSignal): Promise<AdminSupplierDetail> {
  return parseDetail(await request<unknown>(supplierPath(id), { signal }), id)
}

export async function createSupplier(body: CreateSupplierRequest): Promise<AdminSupplierDetail> {
  return parseDetail(await request<unknown>(ROOT, { method: 'POST', body: JSON.stringify(body) }))
}

/** 기본 정보와 인증의 부분 수정. 바꾼 필드만 보낸다 */
export async function patchSupplier(
  id: string,
  currentVersion: number,
  changes: { profile?: Partial<ProfileInput>; certifications?: Partial<CreateSupplierRequest['certifications']> },
): Promise<AdminSupplierDetail> {
  const body: Record<string, unknown> = { version: version(currentVersion) }
  if (changes.profile && Object.keys(changes.profile).length) body.profile = changes.profile
  if (changes.certifications && Object.keys(changes.certifications).length) body.certifications = changes.certifications
  if (!body.profile && !body.certifications) invalidRequest()
  return parseDetail(await request<unknown>(supplierPath(id), { method: 'PATCH', body: JSON.stringify(body) }), id)
}

/** 생산능력 전체 교체 (1~100줄) */
export async function replaceCapabilities(id: string, currentVersion: number, capabilities: CapabilityInput[]): Promise<AdminSupplierDetail> {
  if (capabilities.length < 1 || capabilities.length > 100) invalidRequest()
  const body = JSON.stringify({ version: version(currentVersion), capabilities })
  return parseDetail(await request<unknown>(supplierPath(id, '/capabilities'), { method: 'PUT', body }), id)
}

export async function replaceQuality(id: string, currentVersion: number, quality: QualityInput): Promise<AdminSupplierDetail> {
  const body = JSON.stringify({ version: version(currentVersion), quality })
  return parseDetail(await request<unknown>(supplierPath(id, '/quality'), { method: 'PUT', body }), id)
}

/** 견적은 이력이라 덧붙이기만 한다 (1~100건) */
export async function addQuotes(id: string, currentVersion: number, quotes: QuoteInput[]): Promise<AdminSupplierDetail> {
  if (quotes.length < 1 || quotes.length > 100) invalidRequest()
  const body = JSON.stringify({ version: version(currentVersion), quotes })
  return parseDetail(await request<unknown>(supplierPath(id, '/quotes'), { method: 'POST', body }), id)
}

export async function setSupplierStatus(id: string, currentVersion: number, status: SupplierStatus): Promise<AdminSupplierDetail> {
  if (!STATUSES.includes(status)) invalidRequest()
  const body = JSON.stringify({ version: version(currentVersion), status })
  const detail = parseDetail(await request<unknown>(supplierPath(id, '/status'), { method: 'PATCH', body }), id)
  if (detail.supplier.status !== status) invalidResponse()
  return detail
}
